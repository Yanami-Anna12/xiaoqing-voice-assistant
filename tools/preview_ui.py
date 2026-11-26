"""离屏渲染界面预览图，方便没有图形环境时检查布局。

    python tools/preview_ui.py  →  data/preview.png
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from assistant.config import AppConfig  # noqa: E402
from assistant.ui.main_window import MainWindow  # noqa: E402


class _FakeWorker:
    """预览用：让界面以为语音服务在跑，好展示按钮的"聆听中"状态。"""

    audio_ok = True

    def isRunning(self) -> bool:
        return True


def main() -> int:
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    cfg = AppConfig()
    window = MainWindow(cfg, autostart=False)
    window._worker = _FakeWorker()  # type: ignore[assignment]
    # 挪到屏幕外截图，不打扰用户
    window.move(-4000, -4000)
    window.show()
    app.processEvents()

    # 塞几条演示对话，方便看排版
    window._chat.clear()
    window._chat.add_message("system", "你好，我是小清。喊我的名字就可以开始聊天，也可以直接打字。")
    window._chat.add_message("user", "小清，今天北京天气怎么样", meta="语音识别")
    window._chat.add_message(
        "assistant",
        "北京现在晴，气温 18 摄氏度，体感 17 度，湿度 45%，适合出门走走。",
    )
    window._chat.add_message("user", "我叫阿伟", meta="键盘输入")
    window._chat.add_message("assistant", "你好，阿伟，很高兴认识你。")
    window._on_state("聆听")
    window._on_memory(2, 3)
    window._on_backends(
        {
            "asr": "Vosk 离线中文模型",
            "tts": "Edge-TTS 在线神经语音",
            "llm": "DeepSeek · deepseek-chat",
            "wake": "小清",
        }
    )
    window._mic_label.setText("已连接")
    window._notice.setText("听到：小清，今天北京天气怎么样")

    app.processEvents()
    out = ROOT / "data" / "preview.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    window.grab().save(str(out))
    print(f"已生成：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
