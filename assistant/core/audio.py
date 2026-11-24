"""麦克风采集与端点检测（VAD）。

职责边界很清楚：
- AudioCapture：只负责把麦克风数据搬进队列，不判断语义；
- UtteranceSegmenter：只负责用能量（dBFS）判断"一句话"从哪里开始、到哪里结束；
- 两者都不认识"唤醒词""大模型"，方便单独测试和替换。
"""
from __future__ import annotations

import math
import queue
from collections import deque
from dataclasses import dataclass

import numpy as np

INT16_FULL_SCALE = 32768.0
SILENCE_DBFS = -120.0


def rms_dbfs(chunk: bytes) -> float:
    """一段 16bit PCM 的响度（dBFS），静音约 -90 以下。"""
    if not chunk:
        return SILENCE_DBFS
    samples = np.frombuffer(chunk, dtype=np.int16)
    if samples.size == 0:
        return SILENCE_DBFS
    mean_square = float(np.mean(samples.astype(np.float32) ** 2))
    if mean_square <= 0:
        return SILENCE_DBFS
    rms = math.sqrt(mean_square)
    return 20.0 * math.log10(max(rms / INT16_FULL_SCALE, 1e-12))


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    channels: int

    def __str__(self) -> str:  # 供界面下拉框显示
        return f"{self.name}"


class AudioUnavailable(RuntimeError):
    """没有可用麦克风/驱动，调用方应降级为纯文本模式。"""


class AudioCapture:
    """16kHz / 16bit / 单声道采集，回调里不做重活，立刻丢进队列。"""

    def __init__(self, sample_rate: int = 16000, block_ms: int = 30, device: int | None = None):
        self.sample_rate = sample_rate
        self.block_ms = block_ms
        self.block_bytes = int(sample_rate * block_ms / 1000) * 2
        self.device = device
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=256)
        self._stream = None

    # ---------- 设备 ----------
    @staticmethod
    def list_input_devices() -> list[AudioDevice]:
        try:
            import sounddevice as sd

            devices = []
            for idx, info in enumerate(sd.query_devices()):
                if int(info.get("max_input_channels", 0)) > 0:
                    devices.append(
                        AudioDevice(idx, str(info.get("name", f"设备{idx}")), int(info["max_input_channels"]))
                    )
            return devices
        except Exception:  # noqa: BLE001 - 探测设备失败不应影响主流程
            return []

    @staticmethod
    def available() -> bool:
        return bool(AudioCapture.list_input_devices())

    # ---------- 生命周期 ----------
    def start(self) -> None:
        try:
            import sounddevice as sd
        except Exception as exc:  # noqa: BLE001
            raise AudioUnavailable(f"sounddevice 未安装或加载失败：{exc}") from exc

        if not self.available():
            raise AudioUnavailable("找不到可用的麦克风设备")
        try:
            self._stream = sd.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=int(self.sample_rate * self.block_ms / 1000),
                device=self.device,
                channels=1,
                dtype="int16",
                callback=self._on_audio,
            )
            self._stream.start()
        except Exception as exc:  # noqa: BLE001
            raise AudioUnavailable(f"麦克风打开失败：{exc}") from exc

    def _on_audio(self, indata, _frames, _time_info, status) -> None:  # sounddevice 回调，必须快
        if status:
            # overflow 之类不致命，记录下来继续
            pass
        try:
            self._queue.put_nowait(bytes(indata))
        except queue.Full:
            pass

    def read(self, timeout: float = 0.25) -> bytes | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
        self.drain()

    @property
    def running(self) -> bool:
        return self._stream is not None

    def __enter__(self) -> AudioCapture:
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()


class UtteranceSegmenter:
    """能量 VAD 切句。

    用滞回阈值避免一句话被切成好几截：
    - 静音状态下要超过 start_dbfs 才开始算"说话"；
    - 说话过程中只要高于 continue_dbfs 就继续收音；
    - 连续静音超过 silence_ms 就认为这句结束。

    同时保留 200ms 预录，避免第一个字被切掉。
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        block_ms: int = 30,
        start_dbfs: float = -38.0,
        continue_dbfs: float = -46.0,
        silence_ms: int = 800,
        max_ms: int = 12000,
        min_ms: int = 300,
        preroll_ms: int = 200,
    ):
        self.sample_rate = sample_rate
        self.min_ms = min_ms
        self._preroll = deque(maxlen=max(1, preroll_ms // block_ms))
        self._start_dbfs = start_dbfs
        self._continue_dbfs = continue_dbfs
        self._silence_bytes = int(sample_rate * silence_ms / 1000) * 2
        self._max_bytes = int(sample_rate * max_ms / 1000) * 2
        self.reset()

    # 允许说话中途静音一小会儿（自然停顿）
    def reset(self) -> None:
        self._buffer = bytearray()
        self._silence = 0
        self._active = False
        self._preroll.clear()
        self._spoke = False

    @property
    def active(self) -> bool:
        """正在收音（界面上可以显示"听到你在说"）。"""
        return self._active

    def flush(self) -> bytes | None:
        """手动停止收音时调用：把当前缓冲交出来（没说话返回 None）。"""
        if not self._active:
            self.reset()
            return None
        data = bytes(self._buffer)
        spoke = self._spoke
        self.reset()
        duration_ms = len(data) / 2 / self.sample_rate * 1000
        if spoke and duration_ms >= self.min_ms:
            return data
        return None

    def feed(self, chunk: bytes) -> bytes | None:
        """喂一块音频；凑够一句话返回整句 PCM，否则返回 None。"""
        db = rms_dbfs(chunk)
        threshold = self._continue_dbfs if self._active else self._start_dbfs
        speech = db >= threshold

        if not self._active:
            self._preroll.append(chunk)
            if not speech:
                return None
            self._active = True
            self._buffer = bytearray(b"".join(self._preroll))
            self._preroll.clear()
            self._silence = 0
            self._spoke = True
            return None

        self._buffer.extend(chunk)
        if speech:
            self._silence = 0
        else:
            self._silence += len(chunk)

        if self._silence >= self._silence_bytes or len(self._buffer) >= self._max_bytes:
            data = bytes(self._buffer)
            spoke = self._spoke  # reset() 会清掉，先留一份
            self.reset()
            duration_ms = len(data) / 2 / self.sample_rate * 1000
            if spoke and duration_ms >= self.min_ms:
                return data
        return None
