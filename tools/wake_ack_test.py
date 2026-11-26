"""唤醒应答自检：喊「小清」→ 先应一声（说话）→ 再进入聆听 → 没人说话回待唤醒。

用真实 Vosk 识别合成出来的"小清"，TTS 用静默 Mock（不发声、跑得快）。

    python tools/wake_ack_test.py
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402

import assistant.core.pipeline as pipeline  # noqa: E402
from assistant.config import AppConfig  # noqa: E402


class FakeCapture:
    """按 30ms 一块吐 PCM；喂完就安静。"""

    def __init__(self, pcm: bytes, sample_rate: int = 16000):
        self._pcm = pcm
        self._offset = 0
        self._block = int(sample_rate * 0.03) * 2

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

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
        mp3 = Path(tmp) / "wake.mp3"
        asyncio.run(edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural").save(str(mp3)))
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("缺少 ffmpeg")
        wav = Path(tmp) / "wake.wav"
        subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", str(mp3), "-ar", "16000", "-ac", "1", str(wav)],
            check=True,
        )
        return wav.read_bytes()[44:]


def main() -> int:
    wake_pcm = synth_pcm("小清")
    silent_tail = b"\x00\x00" * 16000 * 2
    print(f"唤醒音频：{len(wake_pcm) / 2 / 16000:.1f}s")

    app = QCoreApplication(sys.argv)
    cfg = AppConfig()
    cfg.asr.backend = "vosk"
    cfg.tts.backend = "mock"
    cfg.tts.enabled = True
    cfg.wake.mode = "voice"
    cfg.wake.ack_enabled = True
    cfg.wake.ack_texts = ["我在呢"]
    cfg.wake.followup_timeout_ms = 1500

    pipeline.AudioCapture = lambda sample_rate=16000, device=None: FakeCapture(wake_pcm + silent_tail)  # type: ignore[assignment]
    worker = pipeline.AssistantWorker(cfg)

    states: list[str] = []
    notices: list[tuple[str, str]] = []
    events: list[tuple[str, int]] = []
    finished = {"done": False}

    def finish() -> None:
        if finished["done"]:
            return
        finished["done"] = True
        worker.shutdown()
        app.quit()

    def on_state(state: str) -> None:
        states.append(state)
        # 聆听后回到待唤醒，说明整个应答+聆听流程走完了，不用等硬超时
        if "聆听" in states and state == "待唤醒":
            QTimer.singleShot(300, finish)

    worker.state_changed.connect(on_state)
    worker.notice.connect(lambda level, text: notices.append((level, text)))
    worker.speech_started.connect(lambda token: events.append(("start", token)))
    worker.speech_finished.connect(lambda token: events.append(("finish", token)))

    started = time.time()
    worker.start()
    QTimer.singleShot(15000, finish)
    app.exec()

    print("状态序列：", " → ".join(states))
    print("朗读事件：", events)
    print("提示：", [text for _, text in notices])
    print(f"耗时：{time.time() - started:.1f}s")

    checks = [
        ("识别到唤醒词", any("唤醒成功" in text for _, text in notices)),
        ("唤醒后有应答语音", ("start", 0) in events and ("finish", 0) in events),
        ("应答文字进提示", any("小清：我在呢" in text for _, text in notices)),
        ("应答后进入聆听", "聆听" in states),
        ("状态先说话后聆听", "说话" in states and states.index("聆听") > states.index("说话")),
        ("没人说话回到待唤醒", states and states[-1] == "待唤醒"),
    ]
    failed = 0
    for name, ok in checks:
        print(("  [PASS] " if ok else "  [FAIL] ") + name)
        failed += 0 if ok else 1
    print(f"\n结果：{'PASS' if failed == 0 else 'FAIL'}（{len(checks) - failed}/{len(checks)}）")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
