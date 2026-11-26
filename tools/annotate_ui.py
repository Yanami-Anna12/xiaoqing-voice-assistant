"""生成"五件事对应界面位置"的标注图（README / 答辩用）。

    python tools/annotate_ui.py
    → docs/images/ui-guide.png
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "images" / "ui-preview.png"
DST = ROOT / "docs" / "images" / "ui-guide.png"

LEGEND_HEIGHT = 200
BG = QColor("#0b1024")

# (编号, 颜色, 高亮区域们, 图例文字)
ITEMS = [
    (
        "1",
        "#5eead4",
        [QRectF(46, 158, 346, 160), QRectF(1010, 245, 292, 112)],
        "唤醒+听写：喊「小清」→ 状态胶囊变「聆听」、波形跳动；识别出的文字带「语音识别」标签进右侧气泡",
    ),
    (
        "2",
        "#818cf8",
        [QRectF(46, 352, 346, 252), QRectF(1000, 18, 240, 70)],
        "接大模型+记忆：「对话记忆 2/3 轮」进度条、「模型」后端行、右上角模型徽章；回答由 DeepSeek 生成",
    ),
    (
        "3",
        "#fbbf24",
        [QRectF(455, 396, 570, 58)],
        "调工具：模型自己决定调用天气/时间/计算器，结果直接写进回答；调用明细记在 data/logs/assistant.log",
    ),
    (
        "4",
        "#a78bfa",
        [QRectF(985, 374, 45, 44), QRectF(1163, 819, 160, 62)],
        "读出来：气泡右上角 🔊 单独朗读这条（朗读中变 ⏹，再点停止）；底部「说话」按钮只控制听你说话",
    ),
    (
        "5",
        "#94a3b8",
        [QRectF(8, 8, 1334, 884)],
        "Qt 界面：整个窗口（标题、状态、波形、气泡、输入栏）都是 PySide6 绘制，「设置」里可改唤醒词",
    ),
]


def main() -> int:
    app = QApplication(sys.argv)  # noqa: F841 - 字体渲染需要
    src = QImage(str(SRC))
    if src.isNull():
        print(f"找不到底图：{SRC}")
        return 1

    canvas = QImage(src.width(), src.height() + LEGEND_HEIGHT, QImage.Format_ARGB32)
    canvas.fill(BG)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.drawImage(0, 0, src)

    # 分隔线
    painter.setPen(QPen(QColor(255, 255, 255, 28), 1))
    painter.drawLine(0, src.height(), src.width(), src.height())

    for index, color_hex, rects, _text in ITEMS:
        color = QColor(color_hex)
        # 区域高亮
        fill = QColor(color)
        fill.setAlpha(26)
        painter.setBrush(fill)
        pen = QPen(color, 2)
        if index == "5":
            pen.setStyle(Qt.DashLine)
            pen.setWidth(2)
        painter.setPen(pen)
        for rect in rects:
            painter.drawRoundedRect(rect, 12, 12)

        # 编号徽章
        badges = {
            "1": QPointF(30, 168),
            "2": QPointF(30, 368),
            "3": QPointF(426, 425),
            "4": QPointF(1052, 396),
            "5": QPointF(24, 26),
        }
        center = badges[index]
        painter.setBrush(color)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(center, 12, 12)
        painter.setPen(QColor("#0b1024"))
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(15)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(center.x() - 12, center.y() - 12, 24, 24), Qt.AlignCenter, index)

    # 图例
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(17)
    painter.setFont(font)
    y = src.height() + 16
    for index, color_hex, _rects, text in ITEMS:
        color = QColor(color_hex)
        painter.setBrush(color)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(34, y + 13), 11, 11)
        painter.setPen(QColor("#0b1024"))
        bold = QFont("Microsoft YaHei UI")
        bold.setPixelSize(14)
        bold.setBold(True)
        painter.setFont(bold)
        painter.drawText(QRectF(23, y + 1, 22, 24), Qt.AlignCenter, index)

        painter.setFont(font)
        painter.setPen(QColor("#e9edf6"))
        painter.drawText(QRectF(58, y, 1260, 26), Qt.AlignVCenter | Qt.AlignLeft, text)
        y += 36

    painter.end()
    DST.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(DST))
    print(f"已生成：{DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
