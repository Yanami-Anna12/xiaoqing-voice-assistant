"""小清语音助手入口。

    python app.py

界面起来后：喊「小清」→ 说指令；或者直接在底部输入框打字。
"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

# 允许 `python app.py` 直接跑，不用先 pip install -e .
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from assistant.config import CONFIG_FILE, PROJECT_ROOT, get_config  # noqa: E402
from assistant.logging_setup import setup_logging  # noqa: E402
from assistant.ui.main_window import MainWindow  # noqa: E402


def _load_dotenv() -> None:
    """支持项目根目录 .env 里写 DEEPSEEK_API_KEY。"""
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except Exception:  # noqa: BLE001 - 没装 dotenv 也能用环境变量
        pass


def _set_taskbar_identity() -> None:
    """让任务栏显示自己的图标，而不是 python.exe。"""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("XiaoQing.VoiceAssistant")
    except Exception:  # noqa: BLE001 - 非 Windows 或权限问题直接忽略
        pass


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="小清语音助手")
    parser.add_argument("--screenshot", default="", help="开发用：启动 N 秒后截图并退出")
    parser.add_argument("--screenshot-delay", type=int, default=6, help="截图前等待秒数")
    args = parser.parse_args()

    log_file = setup_logging()
    _load_dotenv()
    _set_taskbar_identity()

    app = QApplication(sys.argv)
    app.setApplicationName("小清语音助手")
    app.setApplicationDisplayName("小清 · 语音助手")

    font = QFont("Microsoft YaHei UI", 10)
    app.setFont(font)

    window = MainWindow(get_config())
    window.show()
    print(f"日志：{log_file}")
    if not CONFIG_FILE.exists():
        get_config().save()  # 首次运行生成一份可编辑的配置

    if args.screenshot:
        from PySide6.QtCore import QTimer

        def shoot() -> None:
            print("截图：", args.screenshot)
            window.grab().save(args.screenshot)
            window.close()
            app.quit()

        QTimer.singleShot(max(1, args.screenshot_delay) * 1000, shoot)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
