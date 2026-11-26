"""语音合成（TTS）层。

优先级：Edge-TTS 在线神经语音（自然）→ Windows 离线语音（PowerShell 调 System.Speech）→ 静默。
离线后端走独立进程，不把 COM 引擎放在后台线程里（之前踩过析构死锁的坑）。

后端失败只进冷却、不永久剔除：网络抖一下就静音一整个会话是不行的。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import shutil
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from ..config import TTSConfig
from ..logging_setup import get_logger

log = get_logger(__name__)

BACKEND_COOLDOWN_S = 60.0  # 某个后端失败后，冷却这么久再试


def cache_name(text: str) -> str:
    """同一条固定话术（如唤醒应答）复用同一份合成结果。"""
    return "cache_" + hashlib.md5(text.encode("utf-8")).hexdigest()[:12] + ".mp3"


class TTSError(RuntimeError):
    pass


class TTSBackend(ABC):
    name: str = "unknown"

    @abstractmethod
    def speak(self, text: str) -> None:
        """阻塞朗读，读完返回。"""

    def speak_cached(self, text: str) -> None:
        """固定话术朗读：支持的实现会缓存合成结果（如唤醒应答）。"""
        self.speak(text)

    def stop(self) -> None:  # noqa: B027 - 可选钩子
        """打断当前朗读（尽量做到，做不到也不影响主流程）。"""

    def close(self) -> None:  # noqa: B027 - 可选钩子
        pass


class EdgeTTS(TTSBackend):
    name = "Edge-TTS 在线神经语音"

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural", rate: int = 0):
        try:
            import edge_tts  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            raise TTSError(f"edge-tts 未安装：{exc}") from exc
        self._edge_tts = __import__("edge_tts")
        self.voice = voice
        self.rate = rate
        self._tmpdir = Path(tempfile.mkdtemp(prefix="xiaoice_tts_"))
        self._proc: subprocess.Popen | None = None
        self._stopped = False
        self._player = shutil.which("ffplay")

    def _rate_str(self) -> str:
        rate = max(-50, min(50, int(self.rate)))
        return f"{rate:+d}%"

    def speak(self, text: str) -> None:
        if not text.strip():
            return
        if not self._player:
            raise TTSError("缺少 ffplay，无法播放在线语音")
        self._stopped = False
        # 先试流式：合成一块就播一块，省掉整句合成完的 1~2 秒等待
        if self._stream_play(text):
            return
        out = self._tmpdir / f"say_{int(time.time() * 1000)}.mp3"
        try:
            asyncio.run(self._synthesize(text, out))
        except Exception as exc:  # noqa: BLE001
            raise TTSError(f"Edge-TTS 合成失败：{exc}") from exc
        self._play(out, remove_after=True)

    def _stream_play(self, text: str) -> bool:
        """边合成边播；返回 False 表示一个字节都没合成出来，交给文件模式重试。"""
        try:
            proc = subprocess.Popen(
                [self._player, "-nodisp", "-autoexit", "-loglevel", "error", "-f", "mp3", "-i", "pipe:0"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            log.warning("启动播放进程失败：%s", exc)
            return False

        self._proc = proc
        wrote = 0
        first_at: float | None = None
        started = time.perf_counter()
        error: BaseException | None = None

        async def _pump() -> None:
            nonlocal wrote, first_at
            communicate = self._edge_tts.Communicate(text, self.voice, rate=self._rate_str())
            stream = communicate.stream()
            try:
                async for chunk in stream:
                    if self._stopped:
                        break
                    if chunk.get("type") != "audio":
                        continue
                    data = chunk.get("data") or b""
                    if not data:
                        continue
                    proc.stdin.write(data)
                    proc.stdin.flush()
                    wrote += len(data)
                    if first_at is None:
                        first_at = time.perf_counter()
                        log.info("首块音频 %.0fms 到位（边合成边播）", (first_at - started) * 1000)
            finally:
                # 被打断时也要把生成器关掉，否则 aiohttp 连接会泄漏
                aclose = getattr(stream, "aclose", None)
                if aclose is not None:
                    try:
                        await aclose()
                    except Exception:  # noqa: BLE001
                        pass

        try:
            asyncio.run(_pump())
        except Exception as exc:  # noqa: BLE001 - 网络中断、播放进程退出等
            error = exc
        finally:
            try:
                proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                rc = proc.wait(timeout=15)
            except Exception:  # noqa: BLE001
                proc.kill()
                rc = -1
            self._proc = None

        if self._stopped:
            return True
        if wrote == 0:
            log.warning("Edge 流式合成没出音频：%s", error or f"ffplay 退出码 {rc}")
            return False
        if error is not None:
            log.warning("Edge 流式合成中断（已播 %d 字节）：%s", wrote, error)
        return True

    def speak_cached(self, text: str) -> None:
        """固定话术：合成一次落盘，之后直接播放。"""
        if not text.strip():
            return
        if not self._player:
            raise TTSError("缺少 ffplay，无法播放在线语音")
        self._stopped = False
        out = self._tmpdir / cache_name(text)
        if not out.exists() or out.stat().st_size == 0:
            try:
                asyncio.run(self._synthesize(text, out))
            except Exception as exc:  # noqa: BLE001
                raise TTSError(f"Edge-TTS 合成失败：{exc}") from exc
        self._play(out, remove_after=False)

    def _play(self, path: Path, remove_after: bool) -> None:
        if self._stopped:
            return
        self._proc = subprocess.Popen(
            [self._player, "-nodisp", "-autoexit", "-loglevel", "error", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            rc = self._proc.wait()
        finally:
            self._proc = None
            if remove_after:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        if rc != 0 and not self._stopped:
            # 播放进程没正常退出（设备不可用等），交给上层换离线语音
            raise TTSError(f"播放失败（ffplay 退出码 {rc}）")

    async def _synthesize(self, text: str, out: Path) -> None:
        communicate = self._edge_tts.Communicate(text, self.voice, rate=self._rate_str())
        await communicate.save(str(out))

    def stop(self) -> None:
        self._stopped = True
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()

    def close(self) -> None:
        self.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)


class PowerShellSapiTTS(TTSBackend):
    """Windows 自带离线语音（System.Speech）。

    用独立 powershell.exe 进程朗读：可随时 kill、超时可控，也不会出现
    pyttsx3/COM 对象跨线程析构把界面卡死的问题。文本用 base64 传，避免中文编码坑。
    """

    name = "Windows 离线语音"

    def __init__(self, rate: int = 0):
        self._ps = shutil.which("powershell.exe") or shutil.which("powershell")
        if not self._ps:
            raise TTSError("找不到 powershell.exe，无法使用离线语音")
        self._rate = max(-10, min(10, round(rate / 10)))
        self._proc: subprocess.Popen | None = None

    def speak(self, text: str) -> None:
        if not text.strip():
            return
        payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
        script = (
            "Add-Type -AssemblyName System.Speech;"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            f"$s.Rate = {self._rate};"
            "$t = [System.Text.Encoding]::UTF8.GetString("
            f"[System.Convert]::FromBase64String('{payload}'));"
            "$s.Speak($t);"
        )
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        self._proc = subprocess.Popen(
            [self._ps, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            self._proc.wait()
        finally:
            self._proc = None

    def stop(self) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()

    def close(self) -> None:
        self.stop()


class MockTTS(TTSBackend):
    name = "静默模式（无语音输出）"

    def speak(self, text: str) -> None:
        time.sleep(min(3.0, 0.12 * len(text)))


class TTSClient:
    """按优先级串起来的 TTS 门面：一个后端失败就换下一个，冷却后再试。"""

    def __init__(self, cfg: TTSConfig):
        self.cfg = cfg
        self.enabled = cfg.enabled
        self.backends: list[TTSBackend] = []
        self.name = "静默模式（无语音输出）"
        self._active: TTSBackend | None = None
        self._skip_until: dict[int, float] = {}
        self._last_notice = 0.0
        self.on_notice: Callable[[str], None] | None = None
        self._init_backends()

    def _init_backends(self) -> None:
        if self.cfg.backend in ("auto", ""):
            order = ["edge", "sapi"]
        elif self.cfg.backend == "mock":
            order = []
        else:
            order = [self.cfg.backend]
        for kind in order:
            try:
                if kind == "edge":
                    self.backends.append(EdgeTTS(self.cfg.voice, self.cfg.rate))
                elif kind == "sapi":
                    self.backends.append(PowerShellSapiTTS(self.cfg.rate))
            except TTSError as exc:
                log.warning("TTS 后端 %s 不可用：%s", kind, exc)
        if self.backends:
            self._active = self.backends[0]
            self.name = self._active.name
        else:
            self._active = MockTTS()
            self.name = self._active.name

    # ---------------------------------------------------------------- 对外
    def speak(self, text: str) -> None:
        self._speak_via("speak", text)

    def speak_cached(self, text: str) -> None:
        self._speak_via("speak_cached", text)

    def stop(self) -> None:
        if self._active:
            self._active.stop()

    def close(self) -> None:
        for backend in self.backends:
            backend.close()
        self.backends.clear()

    # ---------------------------------------------------------------- 内部
    def _notify(self, message: str, force: bool = False) -> None:
        """降级时告诉用户一声，别让人以为程序坏了。"""
        now = time.time()
        if not force and now - self._last_notice < 3.0:
            return
        self._last_notice = now
        log.warning(message)
        if self.on_notice is not None:
            try:
                self.on_notice(message)
            except Exception:  # noqa: BLE001 - 通知失败不影响朗读
                log.debug("TTS 通知回调失败", exc_info=True)

    def _speak_via(self, method: str, text: str) -> None:
        if not self.enabled:
            return
        now = time.time()
        candidates = [b for b in self.backends if self._skip_until.get(id(b), 0.0) <= now]
        if not candidates:
            candidates = list(self.backends)

        for backend in candidates:
            try:
                self._active = backend
                self.name = backend.name
                action = getattr(backend, method, None) or backend.speak
                action(text)
                return
            except TTSError as exc:
                self._skip_until[id(backend)] = time.time() + BACKEND_COOLDOWN_S
                log.warning("TTS 后端 %s 失败：%s", backend.name, exc)
                self._notify(f"{backend.name} 失败，正在切换后备语音")

        # 全部失败：静音，但要明确告诉用户（这条不能被冷却吞掉）
        self._active = MockTTS()
        self.name = self._active.name
        self._notify("没有可用的语音合成，回答只显示文字", force=True)
        self._active.speak(text)


def create_tts(cfg: TTSConfig) -> TTSClient:
    return TTSClient(cfg)
