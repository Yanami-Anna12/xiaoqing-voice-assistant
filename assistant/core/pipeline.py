"""语音管线（线程）：把"麦克风 → 唤醒 → 识别 → 思考 → 回答 → 朗读"串起来。

设计要点：
- 整个链路跑在一条 QThread 里，界面线程只负责画，不会卡；
- 界面通过 submit_* 把命令投进队列，工作线程通过 Signal 把结果抛回来；
- 状态机只有 5 个状态：待唤醒 / 聆听 / 思考 / 说话（+异常时的离线）。
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QThread, Signal

from ..config import AppConfig
from ..logging_setup import get_logger
from .agent import AssistantAgent, TurnResult
from .asr import ASRBackend, create_asr
from .audio import AudioCapture, AudioUnavailable, UtteranceSegmenter, rms_dbfs
from .llm import BaseLLM, DeepSeekClient, LLMError, MockLLM
from .memory import ConversationMemory
from .tools import build_default_registry
from .tts import TTSClient, create_tts
from .wakeword import WakeWordDetector

log = get_logger(__name__)

STATE_SLEEPING = "待唤醒"
STATE_LISTENING = "聆听"
STATE_THINKING = "思考"
STATE_SPEAKING = "说话"
STATE_OFFLINE = "离线"

MANUAL_LISTEN_MS = 30000  # 手动点"说话"后最多等 30 秒，没人说话就自动回待唤醒


def build_llm(cfg: AppConfig) -> tuple[BaseLLM, str]:
    try:
        return DeepSeekClient(cfg.llm), f"大模型就绪：{cfg.llm.model}"
    except LLMError as exc:
        mock = MockLLM(cfg.llm, default_city=cfg.tools.default_city)
        return mock, f"{exc}；已切换离线演示模型"


@dataclass
class VoiceServices:
    """启动后一次性创建的后端集合，方便界面展示状态。"""

    asr: ASRBackend
    tts: TTSClient
    llm: BaseLLM
    agent: AssistantAgent
    memory: ConversationMemory
    wake: WakeWordDetector
    notes: list[str] = field(default_factory=list)


class AssistantWorker(QThread):
    # ---- 给界面用的信号 ----
    state_changed = Signal(str)
    level_changed = Signal(float)
    user_said = Signal(str, str)  # 内容, 来源(voice/text)
    replied = Signal(str, list)  # 回复, 工具轨迹
    notice = Signal(str, str)  # 级别, 文本
    memory_changed = Signal(int, int)  # 已用轮数, 容量
    backends_ready = Signal(dict)  # 后端信息汇总
    speech_started = Signal(int)  # 某条消息开始朗读（token=0 表示整轮回答）
    speech_finished = Signal(int)  # 该条朗读结束/被打断

    def __init__(self, cfg: AppConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._commands: queue.Queue[tuple[str, object]] = queue.Queue()
        self._stop = False
        self._services: VoiceServices | None = None
        self._audio: AudioCapture | None = None
        self._state = STATE_SLEEPING
        self._speaking = False
        self._listen_deadline = 0.0
        self._audio_ok = False

    # ================================================================ 对外接口
    def submit_text(self, text: str) -> None:
        self._commands.put(("text", text.strip()))

    def listen_start(self) -> None:
        self._commands.put(("listen_start", None))

    def listen_stop(self) -> None:
        self._commands.put(("listen_stop", None))

    def speak_text(self, token: int, text: str) -> None:
        """朗读指定气泡；正在朗读别的就先打断。"""
        services = self._services
        if services and self._speaking:
            services.tts.stop()
        self._commands.put(("speak", (token, text)))

    def stop_speaking(self) -> None:
        services = self._services
        if services and self._speaking:
            services.tts.stop()

    def reset_memory(self) -> None:
        self._commands.put(("reset", None))

    def shutdown(self) -> None:
        self._stop = True
        services = self._services
        if services:
            services.tts.stop()
        self._commands.put(("stop", None))
        self.wait(4000)

    @property
    def audio_ok(self) -> bool:
        return self._audio_ok

    # ================================================================ 线程主体
    def run(self) -> None:  # noqa: C901 - 状态机本身就长
        self._state = STATE_SLEEPING
        self.state_changed.emit(STATE_SLEEPING)
        self.notice.emit("info", "正在初始化语音模型…")

        try:
            services = self._init_services()
        except Exception as exc:  # noqa: BLE001 - 初始化失败也要让界面知道
            log.exception("初始化失败")
            self.state_changed.emit(STATE_OFFLINE)
            self.notice.emit("error", f"初始化失败：{exc}")
            return
        self._services = services
        info = {
            "asr": services.asr.name,
            "tts": services.tts.name,
            "llm": services.agent.model_name,
            "wake": self.cfg.wake.word,
        }
        self.backends_ready.emit(info)
        log.info(
            "启动完成：识别=%s | 播报=%s | 模型=%s | 麦克风=%s",
            info["asr"], info["tts"], info["llm"], "可用" if self._audio_ok else "不可用",
        )
        for note in services.notes:
            self.notice.emit("warn" if "降级" in note or "没有" in note else "info", note)
        if not self.cfg.llm.api_key.strip() and not self.cfg.llm.resolve_api_key():
            self.notice.emit("warn", "没有配置 API Key，正在用离线演示模型回复")
        self.notice.emit("ok", f"准备就绪：喊一声「{self.cfg.wake.word}」试试")

        self._loop()
        self._teardown()

    # ---------------------------------------------------------------- 初始化
    def _init_services(self) -> VoiceServices:
        cfg = self.cfg
        asr, asr_note = create_asr(cfg.asr)
        tts = create_tts(cfg.tts)
        llm, llm_note = build_llm(cfg)
        tools = build_default_registry(cfg.tools)
        memory = ConversationMemory(cfg.llm.memory_rounds)
        agent = AssistantAgent(cfg, llm, tools, memory)
        wake = WakeWordDetector(cfg.wake)
        self.memory_changed.emit(memory.used, memory.capacity_rounds)

        # 麦克风：拿不到就降级成"打字也能用"
        audio = AudioCapture(cfg.asr.sample_rate)
        try:
            audio.start()
            self._audio = audio
            self._audio_ok = True
        except AudioUnavailable as exc:
            self._audio = None
            self._audio_ok = False
            asr_note += f"；麦克风不可用（{exc}）"
            log.warning("麦克风不可用：%s", exc)

        return VoiceServices(
            asr=asr,
            tts=tts,
            llm=llm,
            agent=agent,
            memory=memory,
            wake=wake,
            notes=[asr_note, llm_note, f"语音播报：{tts.name}"],
        )

    def _teardown(self) -> None:
        if self._audio:
            self._audio.stop()
            self._audio = None
        services = self._services
        if services:
            services.tts.close()
            services.asr.close()
        # 在工作线程里把引用放掉：TTS 后端（COM/子进程）在创建它的线程里销毁更安全
        self._services = None
        log.info("工作线程已退出")

    # ---------------------------------------------------------------- 主循环
    def _loop(self) -> None:
        cfg = self.cfg
        segmenter = UtteranceSegmenter(
            sample_rate=cfg.asr.sample_rate,
            start_dbfs=cfg.asr.silence_start_dbfs,
            continue_dbfs=cfg.asr.silence_continue_dbfs,
            silence_ms=cfg.asr.silence_ms,
            max_ms=cfg.asr.max_utterance_ms,
            min_ms=cfg.asr.min_utterance_ms,
        )
        self._set_state(STATE_SLEEPING)

        while not self._stop:
            self._handle_commands(segmenter)
            if self._stop:
                break
            if not self._audio:
                time.sleep(0.05)
                continue

            chunk = self._audio.read(timeout=0.1)
            if not chunk:
                self._check_listen_timeout(segmenter)
                continue

            self.level_changed.emit(_normalize_level(rms_dbfs(chunk)))
            if self._speaking:
                # 朗读时自己的声音会进麦克风，直接丢弃
                continue

            if self._state == STATE_LISTENING:
                segment = segmenter.feed(chunk)
                if segment:
                    self._recognize_and_reply(segment)
                else:
                    self._check_listen_timeout(segmenter)
            elif self._state == STATE_SLEEPING and cfg.wake.mode == "voice":
                segment = segmenter.feed(chunk)
                if segment:
                    self._try_wake(segment, segmenter)

    # ---------------------------------------------------------------- 命令
    def _handle_commands(self, segmenter: UtteranceSegmenter) -> None:
        while True:
            try:
                kind, payload = self._commands.get_nowait()
            except queue.Empty:
                return
            if kind == "stop":
                self._stop = True
                return
            if kind == "text":
                text = str(payload or "").strip()
                if text:
                    segmenter.reset()
                    self._handle_text(text, source="text")
            elif kind == "listen_start":
                if not self._audio_ok:
                    self.notice.emit("warn", "没有可用麦克风，先打字吧")
                    continue
                if self._state == STATE_LISTENING:
                    continue
                self._enter_listening(segmenter, timeout_ms=MANUAL_LISTEN_MS)
            elif kind == "listen_stop":
                self._finish_manual_listen(segmenter)
            elif kind == "speak":
                token, text = payload  # type: ignore[misc]
                self._speak(str(text), token=int(token))
            elif kind == "reset" and self._services:
                self._services.agent.new_session()
                self.memory_changed.emit(0, self._services.memory.capacity_rounds)
                self.notice.emit("info", "已经忘掉之前的对话了")

    # ---------------------------------------------------------------- 唤醒与识别
    def _try_wake(self, segment: bytes, segmenter: UtteranceSegmenter) -> None:
        services = self._services
        if not services:
            return
        try:
            text = services.asr.transcribe(segment, self.cfg.asr.sample_rate)
        except Exception as exc:  # noqa: BLE001
            log.exception("识别失败")
            self.notice.emit("error", f"识别出错：{exc}")
            return
        if not text:
            return
        log.info("唤醒检测听到：%s", text)
        self.notice.emit("info", f"听到：{text}")
        result = services.wake.match(text)
        if not result.woken:
            return

        self.notice.emit("ok", f"唤醒成功（命中「{result.matched}」）")
        command = result.remainder.strip()
        if len(command) >= 2:
            # 一句话唤醒 + 指令，例如"小清，今天天气怎么样"
            self._handle_text(command, source="voice")
        else:
            self._enter_listening(segmenter, timeout_ms=self.cfg.wake.followup_timeout_ms)

    def _recognize_and_reply(self, segment: bytes) -> None:
        services = self._services
        if not services:
            return
        self.level_changed.emit(0.0)
        try:
            text = services.asr.transcribe(segment, self.cfg.asr.sample_rate)
        except Exception as exc:  # noqa: BLE001
            log.exception("识别失败")
            self.notice.emit("error", f"识别出错：{exc}")
            self._set_state(STATE_SLEEPING)
            return
        if not text:
            self.notice.emit("warn", "没听清，再说一次？")
            self._set_state(STATE_SLEEPING)
            return
        self._handle_text(text, source="voice")

    # ---------------------------------------------------------------- 对话
    def _handle_text(self, text: str, source: str) -> None:
        services = self._services
        if not services:
            return
        self.user_said.emit(text, source)
        self._set_state(STATE_THINKING)
        result: TurnResult = services.agent.respond(text)
        self.replied.emit(
            result.text,
            [
                {"name": t.name, "arguments": t.arguments, "result": t.result, "summary": t.summary()}
                for t in result.trace
            ],
        )
        self.memory_changed.emit(services.memory.used, services.memory.capacity_rounds)
        if result.error:
            self.notice.emit("error", result.error)
        log.info("回答耗时 %.0fms：%s", result.latency_ms, result.text)

        if self.cfg.tts.enabled and not self._stop:
            self._speak(result.text)
        # 清掉识别/朗读期间堆积的旧音频，避免把刚才那句话再听一遍
        if self._audio:
            self._audio.drain()
        self._set_state(STATE_SLEEPING)

    def _speech_timeout(self, text: str) -> float:
        """朗读最多等多久：中文约 4 字/秒，再留足合成和播放启动的时间。

        加这个上限是因为：蓝牙耳机没连上、音频设备被占用时，播放进程可能卡住不返回，
        界面会一直停在"停止朗读"。超时就强停，保证按钮一定能回来。
        """
        return max(12.0, 10.0 + len(text) * 0.6)

    def _speak(self, text: str, token: int = 0) -> None:
        services = self._services
        if not services or not text.strip():
            self.speech_finished.emit(token)
            return
        if not self.cfg.tts.enabled:
            self.notice.emit("warn", "语音播报已关闭，文字已经显示在屏幕上了")
            self.speech_finished.emit(token)
            return

        limit = self._speech_timeout(text)
        self._set_state(STATE_SPEAKING)
        self._speaking = True
        self.speech_started.emit(token)
        started = time.perf_counter()
        done = threading.Event()
        error: list[BaseException] = []

        def _run() -> None:
            try:
                services.tts.speak(text)
            except BaseException as exc:  # noqa: BLE001 - 朗读线程里什么异常都不能传出去
                error.append(exc)
            finally:
                done.set()

        job = threading.Thread(target=_run, name="tts-speak", daemon=True)
        try:
            if self._audio:
                self._audio.drain()
            job.start()
            if not done.wait(limit):
                log.warning("朗读超过 %.0fs 仍未结束，强制停止：%s", limit, text[:30])
                services.tts.stop()
                done.wait(2.0)
                self.notice.emit("warn", "朗读超时，已自动停止")
        except Exception as exc:  # noqa: BLE001
            log.exception("朗读失败")
            self.notice.emit("warn", f"朗读失败：{exc}")
        finally:
            self._speaking = False
            if self._audio:
                time.sleep(0.2)
                self._audio.drain()
            log.info("朗读结束：%.1fs / %d 字", time.perf_counter() - started, len(text))
            if error:
                log.warning("朗读线程异常：%r", error[0])
            self.speech_finished.emit(token)
            if not self._stop:
                self._set_state(STATE_SLEEPING)

    # ---------------------------------------------------------------- 状态
    def _enter_listening(self, segmenter: UtteranceSegmenter, timeout_ms: int) -> None:
        segmenter.reset()
        if self._audio:
            self._audio.drain()  # 丢掉唤醒那一刻的尾音
        self._listen_deadline = time.time() + timeout_ms / 1000
        self._set_state(STATE_LISTENING)

    def _finish_manual_listen(self, segmenter: UtteranceSegmenter) -> None:
        """用户手动点"停止聆听"：把已收音频交出去；没声音就回待唤醒。"""
        if self._state != STATE_LISTENING:
            return
        self._listen_deadline = 0.0
        segment = segmenter.flush()
        if not segment:
            self._set_state(STATE_SLEEPING)
            self.notice.emit("warn", "没听到声音，已回到待唤醒")
            return
        self._recognize_and_reply(segment)

    def _check_listen_timeout(self, segmenter: UtteranceSegmenter) -> None:
        if self._state == STATE_LISTENING and self._listen_deadline and time.time() > self._listen_deadline:
            self._listen_deadline = 0.0
            segmenter.reset()
            self._set_state(STATE_SLEEPING)
            self.notice.emit("warn", "没听到内容，我先休息了")

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.state_changed.emit(state)


def _normalize_level(dbfs: float) -> float:
    """-60dBFS → 0，0dBFS → 1，给波形条用。"""
    return max(0.0, min(1.0, (dbfs + 60.0) / 60.0))
