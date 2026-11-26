"""界面接线自检：气泡喇叭、底部「说话」开关的点击是否真的驱动了工作线程。

用假工作线程记录调用，不联网、不出声：

    python tools/ui_wiring_test.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QObject  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from assistant.config import AppConfig  # noqa: E402
from assistant.ui.main_window import MainWindow, SettingsDialog  # noqa: E402


class FakeWorker(QObject):
    audio_ok = True

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple] = []

    def isRunning(self) -> bool:  # noqa: N802 - 模仿 QThread
        return True

    def speak_text(self, token: int, text: str) -> None:
        self.calls.append(("speak", token, text))

    def stop_speaking(self) -> None:
        self.calls.append(("stop",))

    def listen_start(self) -> None:
        self.calls.append(("listen_start",))

    def listen_stop(self) -> None:
        self.calls.append(("listen_stop",))

    def submit_text(self, text: str) -> None:
        self.calls.append(("text", text))

    def reset_memory(self) -> None:
        self.calls.append(("reset",))

    def shutdown(self) -> None:
        self.calls.append(("shutdown",))


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow(AppConfig(), autostart=False)
    fake = FakeWorker()
    window._worker = fake  # type: ignore[assignment]
    app.processEvents()

    checks: list[tuple[str, bool]] = []

    # ---------- 气泡喇叭 ----------
    window._chat.clear()
    bubble = window._chat.add_message("assistant", "这是测试回答")
    checks.append(("气泡上有喇叭按钮", bubble._speak_btn is not None))
    bubble._speak_btn.click()
    token = window._bubble_token.get(bubble)
    checks.append(("点喇叭触发朗读", ("speak", token, "这是测试回答") in fake.calls))

    window._on_speech_started(token)
    checks.append(("朗读中图标变停止", bubble._speak_btn.text() == "⏹"))
    bubble._speak_btn.click()
    checks.append(("再点喇叭触发停止", ("stop",) in fake.calls))
    window._on_speech_finished(token)
    checks.append(("朗读结束图标复原", bubble._speak_btn.text() == "🔊"))

    # 用户气泡也能朗读
    user_bubble = window._chat.add_message("user", "我叫阿伟")
    user_bubble._speak_btn.click()
    checks.append(("用户气泡也能朗读", any(c[0] == "speak" and c[2] == "我叫阿伟" for c in fake.calls)))

    # ---------- 底部说话按钮 ----------
    window._on_state("待唤醒")
    window._mic_btn.click()
    checks.append(("待唤醒点按钮开始聆听", ("listen_start",) in fake.calls))

    window._on_state("聆听")
    checks.append(("聆听中按钮文案", "停止聆听" in window._mic_btn.text()))
    before = fake.calls.count(("listen_stop",))
    window._mic_btn.click()
    checks.append(("聆听中点按钮停止", fake.calls.count(("listen_stop",)) == before + 1))

    window._on_state("说话")
    checks.append(("朗读中按钮不显示停止朗读", window._mic_btn.text() == "🎤 说话"))
    before_stop = fake.calls.count(("stop",))
    before_listen = fake.calls.count(("listen_start",))
    window._mic_btn.click()
    checks.append(
        (
            "朗读中点按钮=打断+开始听",
            fake.calls.count(("stop",)) == before_stop + 1
            and fake.calls.count(("listen_start",)) == before_listen + 1,
        )
    )

    # ---------- 清空记忆 ----------
    window._on_clear_memory()
    checks.append(("清空记忆发出 reset", ("reset",) in fake.calls))

    # ---------- 唤醒应答话术：带逗号的句子不能被拆开 ----------
    cfg2 = AppConfig()
    cfg2.wake.ack_texts = ["哎，我在", "来啦，你说"]
    dialog = SettingsDialog(cfg2, window)
    items = [dialog._ack_list.item(i).text() for i in range(dialog._ack_list.count())]
    checks.append(("带逗号的话术原样进列表", items == ["哎，我在", "来啦，你说"]))
    dialog._ack_list.addItem("嗯，我听着")
    dialog._on_save()
    checks.append(("保存后仍是一句一条", cfg2.wake.ack_texts == ["哎，我在", "来啦，你说", "嗯，我听着"]))

    failed = 0
    for name, ok in checks:
        print(("  [PASS] " if ok else "  [FAIL] ") + name)
        failed += 0 if ok else 1
    print(f"\n结果：{'PASS' if failed == 0 else 'FAIL'}（{len(checks) - failed}/{len(checks)}）")
    window.close()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
