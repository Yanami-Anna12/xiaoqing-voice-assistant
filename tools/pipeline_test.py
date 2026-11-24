"""管线集成自检：真的起一个 QThread，用打字输入走完"思考→工具→回复"。

不依赖麦克风和 API Key（识别用 MockASR，模型未配 Key 时自动走演示模型）。

    python tools/pipeline_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402

from assistant.config import AppConfig  # noqa: E402
from assistant.core.pipeline import AssistantWorker  # noqa: E402


def main() -> int:
    app = QCoreApplication(sys.argv)
    cfg = AppConfig()
    cfg.asr.backend = "mock"
    cfg.tts.enabled = False
    cfg.wake.mode = "push"  # 避免环境噪声误唤醒

    worker = AssistantWorker(cfg)
    result: dict[str, object] = {}
    states: list[str] = []

    def on_replied(text: str, trace: list) -> None:
        result["text"] = text
        result["trace"] = trace
        print(f"\n最终回复：{text}")
        for item in trace:
            print(f"  工具轨迹：{item.get('summary')}")
        worker.shutdown()
        app.quit()

    worker.state_changed.connect(states.append)
    worker.state_changed.connect(lambda s: print(f"[状态] {s}"))
    worker.notice.connect(lambda level, text: print(f"[{level}] {text}"))
    worker.replied.connect(on_replied)

    worker.start()
    QTimer.singleShot(2500, lambda: worker.submit_text("今天北京天气怎么样"))

    def timeout() -> None:
        print("\n[FAIL] 30 秒内没等到回复")
        worker.shutdown()
        app.quit()

    QTimer.singleShot(30000, timeout)
    app.exec()

    text = str(result.get("text") or "")
    trace = result.get("trace") or []
    print("\n观察到的状态序列：", " → ".join(states))
    ok = bool(text) and any(item.get("name") == "get_weather" for item in trace)
    print("结果：", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
