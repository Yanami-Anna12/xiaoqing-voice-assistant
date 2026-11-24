"""语音合成（TTS）层。

优先用 Edge-TTS 的在线神经语音（自然、免费、无需 Key），
没网/没装时退回 Windows 自带的 SAPI 离线语音，最后退回静默的 Mock。
三者接口一致，上层（管线）不需要知情。
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from pathlib import Path

from ..config import TTSConfig


class TTSError(RuntimeError):
    pass


class TTSBackend(ABC):
    name: str = "unknown"

    @abstractmethod
    def speak(self, text: str) -> None:
        """阻塞朗读，读完返回。"""

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
        out = self._tmpdir / f"say_{int(time.time() * 1000)}.mp3"
        try:
            asyncio.run(self._synthesize(text, out))
        except Exception as exc:  # noqa: BLE001
            raise TTSError(f"Edge-TTS 合成失败：{exc}") from exc
        if self._stopped:
            return
        self._proc = subprocess.Popen(
            [self._player, "-nodisp", "-autoexit", "-loglevel", "error", str(out)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            self._proc.wait()
        finally:
            self._proc = None
            try:
                out.unlink(missing_ok=True)
            except OSError:
                pass

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


class SapiTTS(TTSBackend):
    """Windows 自带语音（离线），pyttsx3 封装。"""

    name = "Windows SAPI 离线语音"

    def __init__(self, rate: int = 0):
        try:
            import pyttsx3
        except Exception as exc:  # noqa: BLE001
            raise TTSError(f"pyttsx3 未安装：{exc}") from exc
        self._engine = pyttsx3.init()
        base = 180
        self._engine.setProperty("rate", max(80, min(320, base + int(rate * 1.8))))
        self._pick_chinese_voice()

    def _pick_chinese_voice(self) -> None:
        keywords = ("chinese", "zh-cn", "huihui", "yaoyao", "kangkang", "xiaoxiao", "chinese")
        try:
            for voice in self._engine.getProperty("voices"):
                haystack = f"{getattr(voice, 'name', '')} {getattr(voice, 'id', '')}".lower()
                if any(k in haystack for k in keywords):
                    self._engine.setProperty("voice", voice.id)
                    return
        except Exception:  # noqa: BLE001
            pass

    def speak(self, text: str) -> None:
        if not text.strip():
            return
        try:
            self._engine.say(text)
            self._engine.runAndWait()
        except Exception as exc:  # noqa: BLE001
            raise TTSError(f"SAPI 朗读失败：{exc}") from exc

    def stop(self) -> None:
        try:
            self._engine.stop()
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        self.stop()


class LazySapiTTS(TTSBackend):
    """SAPI 延迟初始化：只有 Edge 真的用不了时才去碰 COM/pyttsx3。

    提前 new 出 pyttsx3 引擎会有两个问题：拖慢启动；对象在别的线程被回收时
    COM 析构可能把界面卡住。所以这里包一层，用到才创建。
    """

    name = "Windows SAPI 离线语音"

    def __init__(self, rate: int = 0):
        self._rate = rate
        self._inner: SapiTTS | None = None

    def _engine(self) -> SapiTTS:
        if self._inner is None:
            self._inner = SapiTTS(self._rate)
        return self._inner

    def speak(self, text: str) -> None:
        self._engine().speak(text)

    def stop(self) -> None:
        if self._inner is not None:
            self._inner.stop()

    def close(self) -> None:
        if self._inner is not None:
            self._inner.close()
            self._inner = None


class MockTTS(TTSBackend):
    name = "静默模式（无语音输出）"

    def speak(self, text: str) -> None:
        time.sleep(min(3.0, 0.12 * len(text)))


class TTSClient:
    """按优先级串起来的 TTS 门面：默认后端失败自动换下一个。"""

    def __init__(self, cfg: TTSConfig):
        self.cfg = cfg
        self.enabled = cfg.enabled
        self.backends: list[TTSBackend] = []
        self.name = "静默模式（无语音输出）"
        self._active: TTSBackend | None = None
        self._init_backends()

    def _init_backends(self) -> None:
        if self.cfg.backend in ("auto", ""):
            # 自动模式只走 Edge；SAPI 的 COM 引擎在后台线程里容易卡死，改为需要时手动指定
            order = ["edge"]
        else:
            order = [self.cfg.backend]
        if self.cfg.backend == "mock":
            order = []
        for kind in order:
            try:
                if kind == "edge":
                    self.backends.append(EdgeTTS(self.cfg.voice, self.cfg.rate))
                elif kind == "sapi":
                    self.backends.append(LazySapiTTS(self.cfg.rate))
            except TTSError:
                continue
        if self.backends:
            self._active = self.backends[0]
            self.name = self._active.name
        else:
            self._active = MockTTS()
            self.name = self._active.name

    def speak(self, text: str) -> None:
        if not self.enabled:
            return
        for backend in list(self.backends):
            try:
                self._active = backend
                self.name = backend.name
                backend.speak(text)
                return
            except TTSError:
                # 坏掉的后端移除，换下一个
                self.backends.remove(backend)
        if not self.backends:
            self._active = MockTTS()
            self.name = self._active.name
            self._active.speak(text)

    def stop(self) -> None:
        if self._active:
            self._active.stop()

    def close(self) -> None:
        for backend in self.backends:
            backend.close()
        self.backends.clear()


def create_tts(cfg: TTSConfig) -> TTSClient:
    return TTSClient(cfg)
