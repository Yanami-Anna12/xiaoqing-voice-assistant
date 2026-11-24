"""语音管线自检：把合成好的一段真实语音"喂"给 AssistantWorker。

不依赖麦克风和人工说话，但走的是完整语音分支：
PCM → VAD 切句 → Vosk 识别 → 唤醒匹配 → 思考 → 工具 → 回复。

    python tools/pipeline_voice_test.py [音频文件]
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402

import assistant.core.pipeline as pipeline  # noqa: E402
from assistant.config import AppConfig  # noqa: E402


class FakeCapture:
    """假装是麦克风：按 30ms 一块吐 PCM，吐完就安静。"""

    def __init__(self, pcm: bytes, sample_rate: int = 16000):
        self._pcm = pcm
        self._offset = 0
        self._block = int(sample_rate * 0.03) * 2
        self.sample_rate = sample_rate
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def drain(self) -> None:
        pass

    def read(self, timeout: float = 0.25) -> bytes | None:
        if self._offset >= len(self._pcm):
            return None
        chunk = self._pcm[self._offset:self._offset + self._block]
        self._offset += self._block
        return chunk


def synth_pcm(text: str) -> bytes:
    import edge_tts

    with tempfile.TemporaryDirectory() as tmp:
        mp3 = Path(tmp) / "voice.mp3"
        asyncio.run(edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural").save(str(mp3)))
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("缺少 ffmpeg")
        wav = Path(tmp) / "voice.wav"
        subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", str(mp3), "-ar", "16000", "-ac", "1", str(wav)],
            check=True,
        )
        return wav.read_bytes()


def main() -> int:
    text = "小清，今天北京天气怎么样"
    print(f"合成语音：{text}")
    wav_pcm = synth_pcm(text)
    # 去掉 44 字节 wav 头
    pcm = wav_pcm[44:]
    print(f"PCM 长度：{len(pcm) / 2 / 16000:.1f}s")

    app = QCoreApplication(sys.argv)
    cfg = AppConfig()
    cfg.tts.enabled = False  # 自检不发声
    cfg.wake.mode = "voice"

    # 结尾补 2 秒静音，否则 VAD 等不到"说完"的静音段
    silent_tail = b"\x00\x00" * 16000 * 2
    fake = FakeCapture(pcm + silent_tail)
    pipeline.AudioCapture = lambda sample_rate=16000: fake  # type: ignore[assignment]

    worker = pipeline.AssistantWorker(cfg)
    result: dict[str, object] = {}
    states: list[str] = []

    def on_replied(answer: str, trace: list) -> None:
        result["text"] = answer
        result["trace"] = trace
        print(f"\n最终回复：{answer}")
        for item in trace:
            print(f"  工具轨迹：{item.get('summary')}")
        worker.shutdown()
        app.quit()

    worker.state_changed.connect(states.append)
    worker.state_changed.connect(lambda s: print(f"[状态] {s}"))
    worker.notice.connect(lambda level, msg: print(f"[{level}] {msg}"))
    worker.user_said.connect(lambda t, src: print(f"[听到/{src}] {t}"))
    worker.replied.connect(on_replied)

    worker.start()
    QTimer.singleShot(40000, lambda: (print("\n[FAIL] 超时"), worker.shutdown(), app.quit()))
    app.exec()

    answer = str(result.get("text") or "")
    trace = result.get("trace") or []
    ok = bool(answer) and any(item.get("name") == "get_weather" for item in trace)
    print("\n状态序列：", " → ".join(states))
    print("结果：", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
