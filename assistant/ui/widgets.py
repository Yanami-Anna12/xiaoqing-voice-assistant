"""界面小组件：状态胶囊、波形条、聊天气泡、聊天区。

都是"只会画图、不懂业务"的纯 UI 组件，数据由 MainWindow 灌进来。

注意：Qt 的样式表会从父控件向子控件级联，所以这里所有样式都写成
带 objectName 的定向选择器（QFrame#Bubble 之类），避免把子控件的背景改掉。
"""
from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .theme import COLORS, STATE_COLORS

BUBBLE_MAX_WIDTH = 520  # 气泡正文最大宽度（像素）
BODY_FONT_PX = 14


def _body_font() -> QFont:
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(BODY_FONT_PX)
    return font


class StatusPill(QWidget):
    """状态胶囊：一个会变色的小圆点 + 状态文字。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Pill")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 16, 8)
        layout.setSpacing(10)

        self._dot = QLabel(self)
        self._dot.setFixedSize(10, 10)
        layout.addWidget(self._dot)

        self._label = QLabel("待唤醒", self)
        layout.addWidget(self._label)
        layout.addStretch(1)

        self.set_state("待唤醒")

    def set_state(self, state: str) -> None:
        color = STATE_COLORS.get(state, COLORS["text_dim"])
        self._label.setText(state)
        self._label.setStyleSheet(f"color: {color}; font-size: 15px; font-weight: 600;")
        self._dot.setStyleSheet(f"background-color: {color}; border-radius: 5px;")
        self.setStyleSheet(
            "QWidget#Pill {"
            f"border: 1px solid {color}44; border-radius: 16px;"
            f"background-color: {color}12;"
            "}"
        )


class Waveform(QWidget):
    """把麦克风音量画成跳动的条，思考/朗读时换成呼吸动画。"""

    def __init__(self, bars: int = 40, parent: QWidget | None = None):
        super().__init__(parent)
        self._levels: deque[float] = deque([0.0] * bars, maxlen=bars)
        self._bars = bars
        self._active = False
        self._state = "待唤醒"
        self._phase = 0.0
        self.setMinimumHeight(62)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    # ---------- 对外 ----------
    def push_level(self, level: float) -> None:
        self._levels.append(max(0.0, min(1.0, level)))

    def set_state(self, state: str) -> None:
        self._state = state
        self._active = state == "聆听"

    # ---------- 绘制 ----------
    def _tick(self) -> None:
        self._phase += 0.12
        if not self._active:
            self._levels = deque((v * 0.88 for v in self._levels), maxlen=self._bars)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width, height = self.width(), self.height()
        n = self._bars
        gap = 4.0
        bar_w = max(2.5, (width - gap * (n - 1)) / n)
        base = height / 2

        gradient = QLinearGradient(0, 0, 0, height)
        gradient.setColorAt(0.0, QColor(COLORS["accent"]))
        gradient.setColorAt(1.0, QColor(COLORS["indigo"]))

        idle_color = QColor(COLORS["text_faint"])
        idle_color.setAlpha(150)

        painter.setPen(Qt.NoPen)
        for i, level in enumerate(self._levels):
            if self._active:
                h = 3 + level * (height - 8)
            elif self._state == "思考":
                h = 4 + 6 * (0.5 + 0.5 * math.sin(self._phase + i * 0.5))
            elif self._state == "说话":
                h = 4 + 10 * (0.5 + 0.5 * math.sin(self._phase * 1.6 + i * 0.35))
            else:
                # 待唤醒：轻轻呼吸，表示还活着
                h = 4 + 3 * (0.5 + 0.5 * math.sin(self._phase * 0.7 + i * 0.25))
            rect = QRectF(i * (bar_w + gap), base - h / 2, bar_w, h)
            path = QPainterPath()
            path.addRoundedRect(rect, bar_w / 2, bar_w / 2)
            painter.setOpacity(1.0 if self._active else 0.75)
            painter.fillPath(path, gradient if self._active else idle_color)
        painter.end()


class ChatBubble(QFrame):
    """一条消息。assistant 在左、user 在右，工具调用挂在气泡底部。

    正文宽度按字体实测宽度算好再固定下来，避免 QLabel 自动换行时
    父容器高度算不对导致文字被裁掉。
    """

    def __init__(
        self,
        role: str,
        text: str,
        meta: str = "",
        on_speak: Callable[[ChatBubble], None] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.role = role
        self.text = text
        self.setObjectName("Bubble")
        is_user = role == "user"
        is_system = role == "system"

        body_width = self._measure(text)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 11, 16, 12)
        outer.setSpacing(7)

        header = QHBoxLayout()
        header.setSpacing(8)
        name = QLabel("你" if is_user else ("小清" if not is_system else "提示"))
        name.setStyleSheet(f"color: {COLORS['text_faint']}; font-size: 11px; letter-spacing: 1px;")
        name.setAttribute(Qt.WA_TransparentForMouseEvents)
        header.addWidget(name)
        if meta:
            meta_label = QLabel(meta)
            meta_label.setStyleSheet(f"color: {COLORS['text_faint']}; font-size: 11px;")
            header.addWidget(meta_label)
        header.addStretch(1)
        self._speak_btn: QPushButton | None = None
        if on_speak is not None and not is_system:
            btn = QPushButton("🔊")
            btn.setObjectName("SpeakBtn")
            btn.setFixedSize(26, 26)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip("朗读这段话")
            btn.setProperty("playing", False)
            btn.clicked.connect(lambda: on_speak(self))
            header.addWidget(btn)
            self._speak_btn = btn
        outer.addLayout(header)

        body = QLabel(text)
        body.setFont(_body_font())
        body.setWordWrap(True)
        body.setFixedWidth(body_width)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")
        outer.addWidget(body)

        if is_system:
            self.setStyleSheet(
                "QFrame#Bubble {"
                f"background-color: rgba(255,255,255,0.03); border: 1px dashed {COLORS['border']}; border-radius: 12px;"
                "}"
            )
        elif is_user:
            self.setStyleSheet(
                "QFrame#Bubble {"
                f"background-color: {COLORS['user_bubble']};"
                f"border: 1px solid {COLORS['user_border']}; border-radius: 16px;"
                "}"
            )
        else:
            self.setStyleSheet(
                "QFrame#Bubble {"
                f"background-color: {COLORS['assistant_bubble']};"
                f"border: 1px solid {COLORS['border']}; border-radius: 16px;"
                "}"
            )

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 5)
        shadow.setColor(QColor(0, 0, 0, 60))
        self.setGraphicsEffect(shadow)

    @staticmethod
    def _measure(text: str) -> int:
        metrics = QFontMetrics(_body_font())
        longest = max((metrics.horizontalAdvance(line) for line in text.split("\n")), default=0)
        return int(min(BUBBLE_MAX_WIDTH, max(180, longest + 6)))

    def set_playing(self, playing: bool) -> None:
        """切换这条消息的"正在朗读"状态：喇叭 ↔ 停止。"""
        if self._speak_btn is None:
            return
        self._speak_btn.setText("⏹" if playing else "🔊")
        self._speak_btn.setToolTip("停止朗读" if playing else "朗读这段话")
        self._speak_btn.setProperty("playing", playing)
        style = self._speak_btn.style()
        style.unpolish(self._speak_btn)
        style.polish(self._speak_btn)


class TypingBubble(QFrame):
    """"小清正在想…"的占位气泡。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Typing")
        self.setStyleSheet(
            "QFrame#Typing {"
            f"background-color: {COLORS['assistant_bubble']};"
            f"border: 1px solid {COLORS['border']}; border-radius: 16px;"
            "}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        label = QLabel("正在思考…")
        label.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 13px; background: transparent;")
        layout.addWidget(label)


class ChatView(QScrollArea):
    """聊天记录区：自动滚动到底部。"""

    def __init__(self, on_speak: Callable[[ChatBubble], None] | None = None, parent=None):
        super().__init__(parent)
        self._on_speak = on_speak
        self.setObjectName("ChatScroll")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)

        self._container = QWidget()
        self._container.setObjectName("ChatInner")
        self._container.setStyleSheet("QWidget#ChatInner { background: transparent; }")
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(6, 4, 6, 8)
        self._layout.setSpacing(14)
        self._layout.addStretch(1)
        self.setWidget(self._container)

    # ---------- 对外 ----------
    def add_message(self, role: str, text: str, meta: str = "") -> ChatBubble:
        bubble = ChatBubble(role, text, meta or time.strftime("%H:%M"), on_speak=self._on_speak)
        row = QWidget()
        row.setAttribute(Qt.WA_TranslucentBackground)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        if role == "user":
            layout.addStretch(1)
            layout.addWidget(bubble)
        elif role == "system":
            layout.addStretch(1)
            layout.addWidget(bubble)
            layout.addStretch(1)
        else:
            layout.addWidget(bubble)
            layout.addStretch(1)
        self._layout.insertWidget(self._layout.count() - 1, row)
        self._scroll_to_bottom()
        return bubble

    def add_typing(self) -> QWidget:
        """插入"正在思考…"占位，返回行控件，方便回复到达后移除。"""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(TypingBubble())
        layout.addStretch(1)
        self._layout.insertWidget(self._layout.count() - 1, row)
        self._scroll_to_bottom()
        return row

    def remove_row(self, row: QWidget) -> None:
        self._layout.removeWidget(row)
        row.setParent(None)
        row.deleteLater()

    def clear(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def welcome(self) -> None:
        self.clear()
        self.add_message(
            "system",
            "你好，我是小清。喊我的名字就可以开始聊天，也可以直接打字。\n"
            "我能查天气、报时间、算数，还会记住我们最近 3 轮对话。",
        )

    # ---------- 内部 ----------
    def _scroll_to_bottom(self) -> None:
        QTimer.singleShot(30, self._do_scroll)

    def _do_scroll(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())
