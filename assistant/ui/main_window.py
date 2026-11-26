"""主窗口：左侧状态 + 右侧对话 + 底部输入。

界面只做三件事：显示状态、显示对话、把用户操作转成命令交给工作线程。
所有耗时逻辑（识别、模型、朗读）都在 AssistantWorker 里跑。
"""
from __future__ import annotations

from typing import ClassVar

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import AppConfig, get_config
from ..core.pipeline import STATE_OFFLINE, AssistantWorker
from .theme import COLORS, STATE_HINTS, build_stylesheet
from .widgets import ChatBubble, ChatView, StatusPill, Waveform


class MainWindow(QMainWindow):
    def __init__(self, cfg: AppConfig | None = None, autostart: bool = True):
        super().__init__()
        self.cfg = cfg or get_config()
        self._worker: AssistantWorker | None = None
        self._typing = None
        self._backends: dict[str, str] = {}
        self._state = "待唤醒"
        self._speech_seq = 0
        self._bubble_token: dict[ChatBubble, int] = {}
        self._token_bubble: dict[int, ChatBubble] = {}
        self._active_speech_token: int | None = None

        self.setWindowTitle("小清 · 语音助手")
        self.resize(self.cfg.ui.width, self.cfg.ui.height)
        self.setMinimumSize(940, 640)
        self.setWindowIcon(self._make_icon())

        self._build_ui()
        self.setStyleSheet(build_stylesheet())
        self._chat.welcome()

        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.timeout.connect(lambda: self._notice.setText(""))

        self._autostart = autostart
        QTimer.singleShot(120, self._start_worker)

    # ================================================================ 界面搭建
    def _build_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(26, 20, 26, 18)
        outer.setSpacing(16)

        outer.addLayout(self._build_header())

        body = QHBoxLayout()
        body.setSpacing(18)
        body.addWidget(self._build_side_panel(), 0)
        body.addWidget(self._build_chat_panel(), 1)
        outer.addLayout(body, 1)

        outer.addLayout(self._build_input_bar())

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(14)

        logo = QLabel("清")
        logo.setFixedSize(46, 46)
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet(
            f"background-color: rgba(94, 234, 212, 0.14);"
            f"border: 1px solid rgba(94, 234, 212, 0.4); border-radius: 23px;"
            f"color: {COLORS['accent']}; font-size: 20px; font-weight: 600;"
        )
        header.addWidget(logo)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("小清 · 语音助手")
        title.setObjectName("AppTitle")
        subtitle = QLabel("唤醒 → 听写 → 大模型思考 → 调工具 → 语音回答")
        subtitle.setObjectName("AppSubtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch(1)

        self._wake_badge = QLabel(f"唤醒词：{self.cfg.wake.word}")
        self._wake_badge.setObjectName("Badge")
        header.addWidget(self._wake_badge)

        self._model_badge = QLabel(self.cfg.llm.model or "未配置模型")
        self._model_badge.setObjectName("BadgeDim")
        self._model_badge.setMaximumWidth(240)
        header.addWidget(self._model_badge)

        settings_btn = QPushButton("设置")
        settings_btn.setObjectName("Ghost")
        settings_btn.clicked.connect(self._open_settings)
        header.addWidget(settings_btn)
        return header

    def _build_side_panel(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        card.setFixedWidth(286)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        label = QLabel("当前状态")
        label.setObjectName("SectionTitle")
        layout.addWidget(label)

        self._status = StatusPill()
        layout.addWidget(self._status)

        self._wave = Waveform()
        layout.addWidget(self._wave)

        self._hint = QLabel(self._hint_text("待唤醒"))
        self._hint.setObjectName("Hint")
        self._hint.setWordWrap(True)
        self._hint.setMinimumHeight(34)
        layout.addWidget(self._hint)

        layout.addWidget(self._divider())

        memory_title = QHBoxLayout()
        caption = QLabel("对话记忆")
        caption.setObjectName("SectionTitle")
        memory_title.addWidget(caption)
        memory_title.addStretch(1)
        self._memory_label = QLabel("0 / 3 轮")
        self._memory_label.setStyleSheet(f"color: {COLORS['text_faint']}; font-size: 11px;")
        memory_title.addWidget(self._memory_label)
        layout.addLayout(memory_title)

        self._memory_bar = QProgressBar()
        self._memory_bar.setObjectName("Memory")
        self._memory_bar.setTextVisible(False)
        self._memory_bar.setRange(0, self.cfg.llm.memory_rounds)
        self._memory_bar.setValue(0)
        layout.addWidget(self._memory_bar)

        layout.addWidget(self._divider())

        backend_title = QLabel("运行后端")
        backend_title.setObjectName("SectionTitle")
        layout.addWidget(backend_title)
        self._asr_label = self._backend_row(layout, "识别", "启动中…")
        self._tts_label = self._backend_row(layout, "播报", "启动中…")
        self._llm_label = self._backend_row(layout, "模型", "启动中…")
        self._mic_label = self._backend_row(layout, "麦克风", "检测中…")

        layout.addStretch(1)

        clear_btn = QPushButton("清空记忆")
        clear_btn.setObjectName("Primary")
        clear_btn.setMinimumHeight(42)
        clear_btn.setToolTip("忘掉最近几轮对话")
        clear_btn.clicked.connect(self._on_clear_memory)
        layout.addWidget(clear_btn)
        return card

    def _backend_row(self, layout: QVBoxLayout, name: str, value: str) -> QLabel:
        row = QHBoxLayout()
        row.setSpacing(8)
        key = QLabel(name)
        key.setStyleSheet(f"color: {COLORS['text_faint']}; font-size: 11px;")
        key.setFixedWidth(38)
        val = QLabel(value)
        val.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 11px;")
        val.setWordWrap(True)
        row.addWidget(key, 0, Qt.AlignTop)
        row.addWidget(val, 1)
        layout.addLayout(row)
        return val

    def _build_chat_panel(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 8)
        layout.setSpacing(8)

        head = QHBoxLayout()
        who = QLabel("对话")
        who.setObjectName("SectionTitle")
        head.addWidget(who)
        head.addStretch(1)
        tip = QLabel("语音和打字都行")
        tip.setStyleSheet(f"color: {COLORS['text_faint']}; font-size: 11px;")
        head.addWidget(tip)
        layout.addLayout(head)

        self._chat = ChatView(on_speak=self._on_speak_bubble)
        layout.addWidget(self._chat, 1)
        return card

    def _build_input_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(12)

        self._notice = QLabel("")
        self._notice.setObjectName("Hint")
        self._notice.setMinimumWidth(180)
        bar.addWidget(self._notice, 1)

        self._input = QLineEdit()
        self._input.setObjectName("Input")
        self._input.setPlaceholderText("也可以直接打字，回车发送…")
        self._input.returnPressed.connect(self._on_send)
        self._input.setMinimumWidth(360)
        bar.addWidget(self._input, 2)

        send = QPushButton("发送")
        send.setObjectName("Primary")
        send.setMinimumHeight(40)
        send.clicked.connect(self._on_send)
        bar.addWidget(send)

        self._mic_btn = QPushButton("🎤 说话")
        self._mic_btn.setObjectName("Mic")
        self._mic_btn.setMinimumHeight(40)
        self._mic_btn.setProperty("active", False)
        self._mic_btn.setToolTip("点一下开始说话，再点一下结束")
        self._mic_btn.clicked.connect(self._on_mic_clicked)
        bar.addWidget(self._mic_btn)
        return bar

    @staticmethod
    def _divider() -> QFrame:
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background-color: {COLORS['border']}; border: none;")
        return line

    def _hint_text(self, state: str) -> str:
        if state == "待唤醒":
            if self.cfg.wake.mode == "push":
                return "点右下角「说话」直接提问，也可以直接打字"
            return f"喊一声「{self.cfg.wake.word}」，或者点右下角「说话」"
        return STATE_HINTS.get(state, "")

    # ================================================================ 工作线程
    def _start_worker(self) -> None:
        if not self._autostart:
            return
        self._stop_worker()
        self._worker = AssistantWorker(self.cfg)
        self._worker.state_changed.connect(self._on_state)
        self._worker.level_changed.connect(self._wave.push_level)
        self._worker.user_said.connect(self._on_user_said)
        self._worker.replied.connect(self._on_replied)
        self._worker.notice.connect(self._on_notice)
        self._worker.memory_changed.connect(self._on_memory)
        self._worker.backends_ready.connect(self._on_backends)
        self._worker.speech_started.connect(self._on_speech_started)
        self._worker.speech_finished.connect(self._on_speech_finished)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()
        self._state = "待唤醒"
        self._sync_mic_button()

    def _stop_worker(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.shutdown()
        self._reset_speech_state()

    def _on_worker_finished(self) -> None:
        self._mic_label.setText("已停止")
        self._mic_label.setStyleSheet(f"color: {COLORS['danger']}; font-size: 11px;")
        self._sync_mic_button()

    # ================================================================ 信号处理
    def _on_state(self, state: str) -> None:
        self._state = state
        self._status.set_state(state)
        self._wave.set_state(state)
        self._hint.setText(self._hint_text(state))
        if state == "思考":
            self._show_typing()
        else:
            self._hide_typing()
        self._sync_mic_button()
        if state == STATE_OFFLINE:
            self._notice.setText("服务没有启动成功，看 data/logs/assistant.log")

    def _on_backends(self, info: dict) -> None:
        self._backends = info
        self._asr_label.setText(info.get("asr", "-"))
        self._tts_label.setText(info.get("tts", "-"))
        self._llm_label.setText(info.get("llm", "-"))
        mic_ok = self._worker.audio_ok if self._worker else False
        self._mic_label.setText("已连接" if mic_ok else "不可用（可打字）")
        self._mic_label.setStyleSheet(
            f"color: {COLORS['ok'] if mic_ok else COLORS['warn']}; font-size: 11px;"
        )
        self._wake_badge.setText(f"唤醒词：{info.get('wake', self.cfg.wake.word)}")
        self._model_badge.setText(info.get("llm", self.cfg.llm.model))

    def _on_user_said(self, text: str, source: str) -> None:
        meta = "语音识别" if source == "voice" else "键盘输入"
        self._chat.add_message("user", text, meta=meta)

    def _on_replied(self, text: str, trace: list) -> None:
        self._hide_typing()
        self._chat.add_message("assistant", text, trace=list(trace or []))

    def _on_notice(self, level: str, text: str) -> None:
        color = {
            "ok": COLORS["ok"],
            "warn": COLORS["warn"],
            "error": COLORS["danger"],
        }.get(level, COLORS["text_dim"])
        self._notice.setText(text)
        self._notice.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._notice_timer.start(8000)

    def _on_memory(self, used: int, capacity: int) -> None:
        self._memory_bar.setRange(0, max(1, capacity))
        self._memory_bar.setValue(used)
        self._memory_label.setText(f"{used} / {capacity} 轮")

    def _show_typing(self) -> None:
        if self._typing is None:
            self._typing = self._chat.add_typing()

    def _hide_typing(self) -> None:
        if self._typing is not None:
            self._chat.remove_row(self._typing)
            self._typing = None

    # ================================================================ 用户操作
    def _on_send(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        if not self._worker or not self._worker.isRunning():
            self._on_notice("warn", "服务还没准备好，稍等一下")
            return
        self._worker.submit_text(text)
        self._input.clear()

    def _on_mic_clicked(self) -> None:
        """底部「说话」按钮：开始/停止聆听；助手朗读时点它=打断并开始听。"""
        if not self._worker or not self._worker.isRunning():
            self._on_notice("warn", "服务还没准备好，稍等一下")
            return
        if self._state == "聆听":
            self._worker.listen_stop()
        elif self._state == "说话":
            self._worker.stop_speaking()  # 先打断朗读
            self._worker.listen_start()  # 再开始听你说
        elif self._state == "思考":
            self._on_notice("warn", "正在思考，稍等一下")
        else:
            self._worker.listen_start()

    def _sync_mic_button(self) -> None:
        running = bool(self._worker and self._worker.isRunning())
        if not running:
            text, active, enabled = "🎤 说话", False, False
        elif self._state == "聆听":
            text, active, enabled = "⏹ 停止聆听", True, True
        elif self._state == "思考":
            text, active, enabled = "思考中…", False, False
        elif self._state == STATE_OFFLINE:
            text, active, enabled = "🎤 说话", False, False
        else:
            # 待唤醒 / 助手朗读中：按钮都保持"说话"，朗读时点它会打断并开始听
            text, active, enabled = "🎤 说话", False, True
        self._mic_btn.setText(text)
        self._mic_btn.setEnabled(enabled)
        self._mic_btn.setProperty("active", active)
        self._mic_btn.setToolTip(
            "点一下开始说话，再点一下结束；助手朗读时点它会打断朗读并开始听"
            if self._state in ("待唤醒", "说话")
            else "正在聆听，点一下结束"
        )
        style = self._mic_btn.style()
        style.unpolish(self._mic_btn)
        style.polish(self._mic_btn)

    def _on_clear_memory(self) -> None:
        if self._worker:
            self._worker.stop_speaking()
        if self._worker and self._worker.isRunning():
            self._worker.reset_memory()
        self._reset_speech_state()
        self._chat.welcome()
        self._on_notice("info", "已经忘掉之前的对话了")

    # ---------------------------------------------------------------- 气泡朗读
    def _on_speak_bubble(self, bubble: ChatBubble) -> None:
        if not self._worker or not self._worker.isRunning():
            self._on_notice("warn", "服务还没准备好，稍等一下")
            return
        token = self._bubble_token.get(bubble)
        if token is None:
            self._speech_seq += 1
            token = self._speech_seq
            self._bubble_token[bubble] = token
            self._token_bubble[token] = bubble
        if self._active_speech_token == token:
            self._worker.stop_speaking()  # 再点一次同一个喇叭 = 停止
        else:
            self._worker.speak_text(token, bubble.text)

    def _on_speech_started(self, token: int) -> None:
        self._active_speech_token = token
        bubble = self._token_bubble.get(token)
        if bubble is not None:
            try:
                bubble.set_playing(True)
            except RuntimeError:  # 气泡已经被清空
                pass

    def _on_speech_finished(self, token: int) -> None:
        bubble = self._token_bubble.get(token)
        if bubble is not None:
            try:
                bubble.set_playing(False)
            except RuntimeError:
                pass
        if self._active_speech_token == token:
            self._active_speech_token = None

    def _reset_speech_state(self) -> None:
        for bubble in list(self._bubble_token):
            try:
                bubble.set_playing(False)
            except RuntimeError:
                continue
        self._bubble_token.clear()
        self._token_bubble.clear()
        self._active_speech_token = None

    # ================================================================ 设置
    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.cfg, self)
        if dialog.exec() == QDialog.Accepted:
            self.cfg.save()
            self._wake_badge.setText(f"唤醒词：{self.cfg.wake.word}")
            self._model_badge.setText(self.cfg.llm.model)
            self._memory_bar.setRange(0, max(1, self.cfg.llm.memory_rounds))
            self._on_notice("info", "设置已保存，正在重启语音服务…")
            self._stop_worker()  # 先停旧的，避免旧线程还在朗读已删除的气泡
            self._chat.welcome()
            QTimer.singleShot(300, self._start_worker)

    # ================================================================ 绘制/退出
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0.0, QColor(COLORS["bg_top"]))
        gradient.setColorAt(0.55, QColor("#101631"))
        gradient.setColorAt(1.0, QColor(COLORS["bg_bottom"]))
        painter.fillRect(self.rect(), gradient)

        painter.setPen(Qt.NoPen)
        glow = QColor(COLORS["accent"])
        glow.setAlpha(16)
        painter.setBrush(glow)
        painter.drawEllipse(QPointF(self.width() * 0.18, self.height() * 0.12), 240, 240)
        glow2 = QColor(COLORS["indigo"])
        glow2.setAlpha(22)
        painter.setBrush(glow2)
        painter.drawEllipse(QPointF(self.width() * 0.88, self.height() * 0.9), 300, 300)
        painter.end()

    def _make_icon(self) -> QIcon:
        pix = QPixmap(64, 64)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(COLORS["accent_deep"]))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(2, 2, 60, 60)
        painter.setPen(QColor("#04211f"))
        font = painter.font()
        font.setPointSize(26)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pix.rect(), Qt.AlignCenter, "清")
        painter.end()
        return QIcon(pix)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_worker()
        super().closeEvent(event)


class SettingsDialog(QDialog):
    """设置：改唤醒词、换模型、填 Key，都在这里。"""

    VOICES: ClassVar[list[tuple[str, str]]] = [
        ("晓晓（女声，自然）", "zh-CN-XiaoxiaoNeural"),
        ("云希（男声，年轻）", "zh-CN-YunxiNeural"),
        ("晓伊（女声，温柔）", "zh-CN-XiaoyiNeural"),
        ("云扬（男声，播报）", "zh-CN-YunyangNeural"),
        ("云健（男声，成熟）", "zh-CN-YunjianNeural"),
    ]

    def __init__(self, cfg: AppConfig, parent: QWidget | None = None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("设置")
        self.setMinimumWidth(520)
        self.setStyleSheet(build_stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        title = QLabel("语音与模型设置")
        title.setObjectName("AppTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight)

        self._wake = QLineEdit(cfg.wake.word)
        form.addRow("唤醒词", self._wake)

        self._aliases = QLineEdit("，".join(cfg.wake.aliases))
        self._aliases.setCursorPosition(0)
        form.addRow("唤醒别名", self._aliases)

        self._wake_mode = QComboBox()
        self._wake_mode.addItem("语音唤醒（喊名字）", "voice")
        self._wake_mode.addItem("按钮唤醒（按开始说话）", "push")
        self._wake_mode.setCurrentIndex(0 if cfg.wake.mode == "voice" else 1)
        form.addRow("唤醒方式", self._wake_mode)

        ack_row = QHBoxLayout()
        ack_row.setSpacing(8)
        self._ack_enabled = QCheckBox("唤醒后应一声")
        self._ack_enabled.setChecked(cfg.wake.ack_enabled)
        ack_row.addWidget(self._ack_enabled)
        self._ack_texts = QLineEdit("，".join(cfg.wake.ack_texts))
        self._ack_texts.setPlaceholderText("用逗号分隔，随机挑一句")
        self._ack_texts.setCursorPosition(0)
        ack_row.addWidget(self._ack_texts, 1)
        form.addRow("唤醒回应", ack_row)

        self._asr_backend = QComboBox()
        self._asr_backend.addItem("Vosk 本地模型", "vosk")
        self._asr_backend.addItem("演示识别（不出声也能跑流程）", "mock")
        self._asr_backend.setCurrentIndex(0 if cfg.asr.backend == "vosk" else 1)
        form.addRow("识别后端", self._asr_backend)

        self._alias_city = QLineEdit(cfg.tools.default_city)
        form.addRow("默认城市", self._alias_city)

        self._base_url = QLineEdit(cfg.llm.base_url)
        form.addRow("模型地址", self._base_url)

        self._model = QLineEdit(cfg.llm.model)
        form.addRow("模型名称", self._model)

        self._api_key = QLineEdit()
        self._api_key.setEchoMode(QLineEdit.Password)
        self._api_key.setPlaceholderText(f"留空则读环境变量 {cfg.llm.api_key_env}")
        form.addRow("API Key", self._api_key)

        self._memory_rounds = QSpinBox()
        self._memory_rounds.setRange(1, 10)
        self._memory_rounds.setValue(cfg.llm.memory_rounds)
        form.addRow("记忆轮数", self._memory_rounds)

        self._tts_enabled = QCheckBox("语音朗读回答")
        self._tts_enabled.setChecked(cfg.tts.enabled)
        form.addRow("语音", self._tts_enabled)

        self._tts_voice = QComboBox()
        for label, value in self.VOICES:
            self._tts_voice.addItem(label, value)
        index = self._tts_voice.findData(cfg.tts.voice)
        self._tts_voice.setCurrentIndex(max(0, index))
        form.addRow("音色", self._tts_voice)

        layout.addLayout(form)

        hint = QLabel("API Key 只在本地保存；也可以设置环境变量，设置优先用输入框。")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_save(self) -> None:
        wake = self._wake.text().strip()
        if not wake:
            QMessageBox.warning(self, "提示", "唤醒词不能为空")
            return
        self.cfg.wake.word = wake
        aliases = [a.strip() for a in self._aliases.text().replace("，", ",").split(",") if a.strip()]
        self.cfg.wake.aliases = aliases
        self.cfg.wake.mode = self._wake_mode.currentData()
        self.cfg.wake.ack_enabled = self._ack_enabled.isChecked()
        ack_texts = [a.strip() for a in self._ack_texts.text().replace("，", ",").split(",") if a.strip()]
        if ack_texts:
            self.cfg.wake.ack_texts = ack_texts
        self.cfg.asr.backend = self._asr_backend.currentData()
        self.cfg.tools.default_city = self._alias_city.text().strip() or "北京"
        self.cfg.llm.base_url = self._base_url.text().strip() or self.cfg.llm.base_url
        self.cfg.llm.model = self._model.text().strip() or self.cfg.llm.model
        key = self._api_key.text().strip()
        if key:
            self.cfg.llm.api_key = key
        self.cfg.llm.memory_rounds = self._memory_rounds.value()
        self.cfg.tts.enabled = self._tts_enabled.isChecked()
        self.cfg.tts.voice = self._tts_voice.currentData()
        self.accept()
