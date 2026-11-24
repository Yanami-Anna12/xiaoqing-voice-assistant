"""朗读看门狗自检：TTS 卡住不返回时，状态和按钮必须能回来。

    python tools/tts_watchdog_test.py
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402

import assistant.core.pipeline as pipeline  # noqa: E402
from assistant.config import AppConfig  # noqa: E402


class SilentCapture:
    def __init__(self, sample_rate: int = 16000):
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def drain(self) -> None:
        pass

    def read(self, timeout: float = 0.25) -> bytes | None:
        time.sleep(0.02)
        return None


class HangingTTS:
    """模拟蓝牙耳机没连上、播放进程卡死的情况。"""

    name = "卡住的 TTS（测试用）"

    def __init__(self) -> None:
        self.released = threading.Event()

    def speak(self, text: str) -> None:
        self.released.wait(30)  # 一直卡着，直到 stop() 被调用

    def stop(self) -> None:
        self.released.set()

    def close(self) -> None:
        self.released.set()


def main() -> int:
    app = QCoreApplication(sys.argv)
    cfg = AppConfig()
    cfg.asr.backend = "mock"
    cfg.tts.backend = "mock"  # 别初始化 SAPI，测试里直接换成卡住的假实现
    cfg.tts.enabled = True
    cfg.wake.mode = "push"

    pipeline.AudioCapture = lambda sample_rate=16000: SilentCapture()  # type: ignore[assignment]
    worker = pipeline.AssistantWorker(cfg)

    events: list[tuple[str, int]] = []
    notices: list[tuple[str, str]] = []
    states: list[str] = []
    result: dict[str, object] = {}
    started_at: dict[str, float] = {}

    worker.state_changed.connect(states.append)
    worker.notice.connect(lambda level, text: notices.append((level, text)))
    worker.speech_started.connect(lambda token: (events.append(("start", token)), started_at.setdefault("t", time.time())))
    worker.speech_finished.connect(lambda token: (events.append(("finish", token)), result.setdefault("elapsed", time.time() - started_at.get("t", time.time()))))

    def step_speak() -> None:
        stub = HangingTTS()
        services = worker._services
        assert services is not None
        # 只替换方法，不替换整个 TTSClient：替换对象会在主线程触发旧 SAPI 对象的 COM 析构，测试会卡住
        services.tts.speak = stub.speak  # type: ignore[method-assign]
        services.tts.stop = stub.stop  # type: ignore[method-assign]
        worker._speech_timeout = lambda text: 1.5  # type: ignore[method-assign]
        worker.speak_text(9, "这句会卡住")
        QTimer.singleShot(4000, step_check)

    def step_check() -> None:
        result["final_state"] = states[-1] if states else ""
        result["notices"] = list(notices)
        worker.shutdown()
        app.quit()

    worker.start()
    QTimer.singleShot(2500, step_speak)
    QTimer.singleShot(12000, lambda: (worker.shutdown(), app.quit()))
    app.exec()

    elapsed = float(result.get("elapsed") or 99)
    print("状态序列：", " → ".join(states))
    print("事件：", events, f"耗时 {elapsed:.1f}s")
    print("提示：", [text for _, text in notices])

    checks = [
        ("卡住时也能发出 speech_finished", ("finish", 9) in events),
        ("超时耗时接近看门狗上限", elapsed < 4.0),
        ("结束后状态回到待唤醒", result.get("final_state") == "待唤醒"),
        ("有朗读超时提示", any("超时" in text for _, text in notices)),
    ]
    failed = 0
    for name, ok in checks:
        print(("  [PASS] " if ok else "  [FAIL] ") + name)
        failed += 0 if ok else 1
    print(f"\n结果：{'PASS' if failed == 0 else 'FAIL'}（{len(checks) - failed}/{len(checks)}）")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
