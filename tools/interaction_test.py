"""交互自检：底部「说话」开关、无声音回待唤醒、气泡朗读开/停。

用静音假麦克风 + MockTTS，不依赖真人、不发声：

    python tools/interaction_test.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402

import assistant.core.pipeline as pipeline  # noqa: E402
from assistant.config import AppConfig  # noqa: E402


class SilentCapture:
    """假装麦克风一直安静（用于验证"没听到声音"分支）。"""

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def drain(self) -> None:
        pass

    def read(self, timeout: float = 0.25) -> bytes | None:
        time.sleep(0.02)
        return None


def main() -> int:
    app = QCoreApplication(sys.argv)
    cfg = AppConfig()
    cfg.asr.backend = "mock"
    cfg.tts.backend = "mock"  # 不联网、不出声
    cfg.tts.enabled = True
    cfg.wake.mode = "push"

    pipeline.AudioCapture = lambda sample_rate=16000, device=None: SilentCapture()  # type: ignore[assignment]
    worker = pipeline.AssistantWorker(cfg)

    states: list[str] = []
    notices: list[tuple[str, str]] = []
    events: list[tuple[str, int]] = []
    result: dict[str, object] = {}

    worker.state_changed.connect(states.append)
    worker.notice.connect(lambda level, text: notices.append((level, text)))
    worker.speech_started.connect(lambda token: events.append(("start", token)))
    worker.speech_finished.connect(lambda token: events.append(("finish", token)))

    def step_start_listen() -> None:
        worker.listen_start()
        QTimer.singleShot(900, step_stop_listen)

    def step_stop_listen() -> None:
        result["listen_state"] = states[-1] if states else ""
        worker.listen_stop()
        QTimer.singleShot(900, step_speak)

    def step_speak() -> None:
        result["after_stop_state"] = states[-1] if states else ""
        result["notices"] = list(notices)
        worker.speak_text(7, "这是一句测试朗读")
        QTimer.singleShot(3500, step_finish)

    def step_finish() -> None:
        result["speaking_states"] = list(states)
        result["events"] = list(events)
        worker.shutdown()
        app.quit()

    worker.start()
    QTimer.singleShot(2500, step_start_listen)
    QTimer.singleShot(15000, lambda: (worker.shutdown(), app.quit()))
    app.exec()

    print("状态序列：", " → ".join(states))
    print("朗读事件：", events)
    print("提示：", [text for _, text in notices])

    checks = [
        ("点开始后进入「聆听」", result.get("listen_state") == "聆听"),
        ("点停止后回到「待唤醒」", result.get("after_stop_state") == "待唤醒"),
        ("没听到声音有提示", any("没听到声音" in text for _, text in notices)),
        ("朗读开始事件", ("start", 7) in events),
        ("朗读结束事件", ("finish", 7) in events),
        ("朗读时状态为「说话」", "说话" in states),
    ]
    failed = 0
    for name, ok in checks:
        print(("  [PASS] " if ok else "  [FAIL] ") + name)
        failed += 0 if ok else 1
    print(f"\n结果：{'PASS' if failed == 0 else 'FAIL'}（{len(checks) - failed}/{len(checks)}）")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
