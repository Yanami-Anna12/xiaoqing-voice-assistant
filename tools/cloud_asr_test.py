"""云端识别自检（可选，需要 DASHSCOPE_API_KEY）：唤醒走本地 Vosk，指令走 Paraformer。

    python tools/cloud_asr_test.py

不联网或没有 Key 时会打印 SKIP，不算失败。
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
from assistant.config import AppConfig, read_env  # noqa: E402


class FakeCapture:
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
        mp3 = Path(tmp) / "a.mp3"
        asyncio.run(edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural").save(str(mp3)))
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("缺少 ffmpeg")
        wav = Path(tmp) / "a.wav"
        subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", str(mp3), "-ar", "16000", "-ac", "1", str(wav)],
            check=True,
        )
        return wav.read_bytes()[44:]


def main() -> int:
    if not read_env("DASHSCOPE_API_KEY"):
        print("SKIP：没有 DASHSCOPE_API_KEY")
        return 0

    silence = b"\x00\x00" * 16000 * 2
    wake_pcm = synth_pcm("小清")
    cmd_pcm = synth_pcm("今天北京天气怎么样，顺便说一下明天会不会下雨")
    pcm = wake_pcm + silence + cmd_pcm + silence

    app = QCoreApplication(sys.argv)
    cfg = AppConfig()
    cfg.asr.backend = "hybrid"
    cfg.tts.backend = "mock"  # 自检不出声
    cfg.tts.enabled = True
    cfg.wake.mode = "voice"
    cfg.wake.ack_enabled = True
    cfg.wake.ack_texts = ["我在呢"]
    cfg.wake.followup_timeout_ms = 8000

    pipeline.AudioCapture = lambda sample_rate=16000, device=None: FakeCapture(pcm)  # type: ignore[assignment]
    worker = pipeline.AssistantWorker(cfg)

    heard: list[tuple[str, str]] = []
    states: list[str] = []
    notices: list[tuple[str, str]] = []
    result: dict[str, object] = {}
    finished = {"done": False}

    def finish() -> None:
        if finished["done"]:
            return
        finished["done"] = True
        result["cmd_backend"] = getattr(worker._services.asr_cmd, "name", "")
        worker.shutdown()
        app.quit()

    def on_user(text: str, source: str) -> None:
        heard.append((text, source))

    def on_replied(text: str, trace: list) -> None:
        result["reply"] = text
        QTimer.singleShot(300, finish)

    worker.user_said.connect(on_user)
    worker.state_changed.connect(states.append)
    worker.notice.connect(lambda level, text: notices.append((level, text)))
    worker.replied.connect(on_replied)
    worker.start()
    QTimer.singleShot(30000, finish)
    app.exec()

    command_text = heard[-1][0] if heard else ""
    print("状态序列：", " → ".join(states))
    print("指令后端：", result.get("cmd_backend"))
    print("识别文本：", command_text)
    print("回复：", result.get("reply"))
    print("提示：", [t for _, t in notices if "识别" in t or "唤醒" in t])

    checks = [
        ("口令走了云端识别", "DashScope" in str(result.get("cmd_backend"))),
        ("识别文本基本正确", ("天气" in command_text) and ("明天" in command_text or "下雨" in command_text)),
        ("云端结果带标点", any(p in command_text for p in "，。？")),
        ("拿到了回复", bool(result.get("reply"))),
    ]
    failed = 0
    for name, ok in checks:
        print(("  [PASS] " if ok else "  [FAIL] ") + name)
        failed += 0 if ok else 1
    print(f"\n结果：{'PASS' if failed == 0 else 'FAIL'}（{len(checks) - failed}/{len(checks)}）")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
