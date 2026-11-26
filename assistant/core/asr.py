"""语音识别（ASR）层。

对外只暴露一个方法：transcribe(一段 16k/16bit 单声道 PCM) -> 中文文本。
底层实现可替换：本地 Vosk 小模型（默认）、演示用的 MockASR。
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from ..config import ASRConfig
from ..logging_setup import get_logger

log = get_logger(__name__)


class ASRError(RuntimeError):
    pass


def resolve_model_dir(model_path: str | Path) -> Path:
    """返回 Vosk 能打开的模型目录。

    Vosk 的 C++ 层在 Windows 上按 ANSI 打开路径，项目放在中文目录里会
    报 "Failed to create a model"。检测到非 ASCII 路径时，自动复制一份到
    %LOCALAPPDATA%\\XiaoQing\\models（纯英文路径）再加载。只复制一次。
    """
    src = Path(model_path)
    if not src.exists() or str(src).isascii():
        return src

    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    cache = Path(base) / "XiaoQing" / "models" / src.name
    if not (cache / "am" / "final.mdl").exists():
        log.info("模型路径含中文，复制到 %s 后加载（只复制一次）", cache)
        cache.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, cache, dirs_exist_ok=True)
    return cache


class ASRBackend(ABC):
    name: str = "unknown"
    offline: bool = True
    streaming: bool = False  # 能否边收音频边解码（能的话几乎不用等）

    @abstractmethod
    def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        """整句识别，返回去掉空格的中文文本（可能为空串）。"""

    def start_stream(self, sample_rate: int = 16000) -> None:  # noqa: B027 - 可选钩子
        """开始/重置一段流式识别。"""

    def feed_stream(self, pcm: bytes) -> None:  # noqa: B027 - 可选钩子
        """喂一块实时音频。"""

    def finish_stream(self) -> str:
        """结束这段流式识别，拿到结果。"""
        return ""

    def warmup(self) -> None:  # noqa: B027 - 可选钩子
        """可选：提前加载模型，避免第一句话等太久。"""

    def close(self) -> None:  # noqa: B027 - 可选钩子，子类按需覆盖
        pass


class VoskASR(ASRBackend):
    """离线中文识别，模型约 40MB，CPU 实时性足够。"""

    name = "Vosk 离线中文模型"
    streaming = True

    def __init__(self, model_path: str):
        self.requested_path = Path(model_path)
        if not self.requested_path.exists():
            raise ASRError(f"找不到 Vosk 模型目录：{self.requested_path}")
        self.model_path = resolve_model_dir(self.requested_path)
        try:
            import vosk

            vosk.SetLogLevel(-1)
            t0 = time.perf_counter()
            self._model = vosk.Model(str(self.model_path))
        except Exception as exc:  # noqa: BLE001
            raise ASRError(f"Vosk 模型加载失败（{self.model_path}）：{exc}") from exc
        self._vosk = vosk
        self.load_seconds = time.perf_counter() - t0
        self._rec = None
        self._stream_rate = 16000

    # ---------- 整句识别 ----------
    def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        if not pcm:
            return ""
        rec = self._vosk.KaldiRecognizer(self._model, sample_rate)
        chunk = 4000
        for i in range(0, len(pcm), chunk):
            rec.AcceptWaveform(pcm[i:i + chunk])
        result = json.loads(rec.FinalResult() or "{}")
        return str(result.get("text", "")).replace(" ", "")

    # ---------- 流式识别：边听边解码，说完就有字 ----------
    def start_stream(self, sample_rate: int = 16000) -> None:
        self._stream_rate = sample_rate
        self._rec = self._vosk.KaldiRecognizer(self._model, sample_rate)

    def feed_stream(self, pcm: bytes) -> None:
        if self._rec is not None and pcm:
            self._rec.AcceptWaveform(pcm)

    def finish_stream(self) -> str:
        rec, self._rec = self._rec, None
        if rec is None:
            return ""
        result = json.loads(rec.FinalResult() or "{}")
        return str(result.get("text", "")).replace(" ", "")

    def close(self) -> None:
        self._rec = None
        self._model = None


class DashScopeASR(ASRBackend):
    """阿里云 Paraformer 实时识别（走 DashScope，需联网和 DASHSCOPE_API_KEY）。

    只用在"唤醒之后的指令"上：唤醒词继续用本地 Vosk，这样环境噪声再多也不会
    一直烧云端额度。准确率比 vosk-model-small-cn 高一档，带标点。
    """

    name = "DashScope Paraformer（云端）"
    streaming = True

    def __init__(self, api_key: str, model: str = "paraformer-realtime-v2"):
        try:
            import dashscope
        except Exception as exc:  # noqa: BLE001
            raise ASRError(f"dashscope 未安装：{exc}") from exc
        if not api_key.strip():
            raise ASRError("没有 DASHSCOPE_API_KEY，无法用云端识别")
        dashscope.api_key = api_key.strip()
        self._dashscope = dashscope
        self.model = model
        self._rec = None
        self._parts: list[str] = []
        self._done = threading.Event()
        self._error = ""

    def start_stream(self, sample_rate: int = 16000) -> None:
        from dashscope.audio.asr import Recognition, RecognitionCallback

        self._parts = []
        self._error = ""
        self._done.clear()
        owner = self

        class _Callback(RecognitionCallback):
            def on_open(self) -> None:
                pass

            def on_event(self, result) -> None:
                try:
                    sentence = result.get_sentence()
                except Exception:  # noqa: BLE001
                    return
                if isinstance(sentence, dict) and sentence.get("sentence_end"):
                    text = str(sentence.get("text") or "").strip()
                    if text:
                        owner._parts.append(text)

            def on_complete(self) -> None:
                owner._done.set()

            def on_error(self, result) -> None:
                owner._error = str(result)[:200]
                owner._done.set()

            def on_close(self) -> None:
                pass

        self._rec = Recognition(
            model=self.model,
            callback=_Callback(),
            format="pcm",
            sample_rate=sample_rate,
        )
        self._rec.start()

    def feed_stream(self, pcm: bytes) -> None:
        if self._rec is None or not pcm:
            return
        try:
            self._rec.send_audio_frame(pcm)
        except Exception as exc:  # noqa: BLE001
            self._error = f"发送音频失败：{exc}"

    def finish_stream(self) -> str:
        rec, self._rec = self._rec, None
        if rec is None:
            return ""
        try:
            rec.stop()
        except Exception as exc:  # noqa: BLE001
            self._error = f"结束识别失败：{exc}"
        self._done.wait(8.0)
        text = "".join(self._parts).strip()
        if not text and self._error:
            log.warning("云端识别失败：%s", self._error)
        return text

    def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        self.start_stream(sample_rate)
        for i in range(0, len(pcm), 3200):
            self.feed_stream(pcm[i:i + 3200])
        return self.finish_stream()

    def cancel(self) -> None:
        """不等结果直接关掉连接（聆听超时、没说话时用）。"""
        rec, self._rec = self._rec, None
        if rec is not None:
            try:
                rec.stop()
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        self.cancel()


class MockASR(ASRBackend):
    """没有语音模型时的演示后端：按台词轮播，保证主流程能跑通。"""

    name = "演示识别（未加载语音模型）"

    SCRIPT: ClassVar[list[str]] = [
        "小清",
        "今天北京天气怎么样",
        "你好，我叫阿伟",
        "你还记得我叫什么吗",
        "现在几点了",
        "帮我算一下 128 乘以 7",
    ]

    def __init__(self) -> None:
        self._i = 0

    def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        time.sleep(0.2)  # 模拟推理耗时
        text = self.SCRIPT[self._i % len(self.SCRIPT)]
        self._i += 1
        return text


def create_asr(cfg: ASRConfig) -> tuple[ASRBackend, str]:
    """按配置创建识别后端；失败自动降级到 MockASR。

    返回 (后端, 提示语)。提示语会显示在界面状态栏，避免"静默降级"。
    """
    backend = (cfg.backend or "vosk").lower()
    if backend == "mock":
        return MockASR(), "当前为演示识别模式"

    try:
        asr = VoskASR(cfg.model_path)
        return asr, f"语音识别就绪（Vosk，{asr.load_seconds:.1f}s 加载）"
    except ASRError as exc:
        return MockASR(), f"{exc}；已降级为演示识别"
