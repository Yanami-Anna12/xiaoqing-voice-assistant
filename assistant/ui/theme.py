"""配色与全局样式表。

界面是深色玻璃风格：深蓝紫渐变底 + 半透明卡片 + 青绿点缀，
状态色沿用"红黄绿蓝"的直觉，答辩时一眼能看出助手在干什么。
"""
from __future__ import annotations

COLORS = {
    "bg_top": "#0a1024",
    "bg_bottom": "#151a33",
    "card": "rgba(255, 255, 255, 0.045)",
    "card_hover": "rgba(255, 255, 255, 0.07)",
    "border": "rgba(255, 255, 255, 0.09)",
    "text": "#e9edf6",
    "text_dim": "#9aa5bd",
    "text_faint": "#6b7691",
    "accent": "#5eead4",
    "accent_deep": "#0ea5a3",
    "indigo": "#6366f1",
    "user_bubble": "rgba(99, 102, 241, 0.22)",
    "user_border": "rgba(129, 140, 248, 0.45)",
    "assistant_bubble": "rgba(255, 255, 255, 0.055)",
    "danger": "#f87171",
    "warn": "#fbbf24",
    "ok": "#34d399",
}

STATE_COLORS = {
    "待唤醒": "#8b96ad",
    "聆听": "#34d399",
    "思考": "#fbbf24",
    "说话": "#a78bfa",
    "离线": "#f87171",
}

STATE_HINTS = {
    "待唤醒": "喊一声「{wake}」，或者按右边的「开始说话」",
    "聆听": "我在听，请说…",
    "思考": "正在问大模型…",
    "说话": "正在回答…",
    "离线": "服务没起来，看下日志里第一条报错",
}


def build_stylesheet() -> str:
    c = COLORS
    return f"""
    QWidget {{
        color: {c['text']};
        font-family: "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", sans-serif;
        font-size: 14px;
    }}
    QToolTip {{
        background-color: #1d2440;
        color: {c['text']};
        border: 1px solid {c['border']};
        padding: 6px 8px;
    }}

    /* ---------- 卡片 ---------- */
    QFrame#Card {{
        background-color: {c['card']};
        border: 1px solid {c['border']};
        border-radius: 16px;
    }}

    /* ---------- 标题 ---------- */
    QLabel#AppTitle {{
        font-size: 19px;
        font-weight: 600;
        color: {c['text']};
        letter-spacing: 1px;
    }}
    QLabel#AppSubtitle {{
        font-size: 12px;
        color: {c['text_faint']};
    }}
    QLabel#SectionTitle {{
        font-size: 12px;
        color: {c['text_faint']};
        letter-spacing: 2px;
    }}
    QLabel#Hint {{
        color: {c['text_dim']};
        font-size: 12px;
    }}

    /* ---------- 徽章 ---------- */
    QLabel#Badge {{
        background-color: rgba(94, 234, 212, 0.10);
        color: {c['accent']};
        border: 1px solid rgba(94, 234, 212, 0.25);
        border-radius: 10px;
        padding: 3px 10px;
        font-size: 12px;
    }}
    QLabel#BadgeDim {{
        background-color: rgba(255, 255, 255, 0.06);
        color: {c['text_dim']};
        border: 1px solid {c['border']};
        border-radius: 10px;
        padding: 3px 10px;
        font-size: 12px;
    }}

    /* ---------- 输入框 ---------- */
    QLineEdit#Input {{
        background-color: rgba(255, 255, 255, 0.055);
        border: 1px solid {c['border']};
        border-radius: 22px;
        padding: 10px 18px;
        font-size: 14px;
        selection-background-color: {c['indigo']};
    }}
    QLineEdit#Input:focus {{
        border: 1px solid rgba(94, 234, 212, 0.55);
        background-color: rgba(255, 255, 255, 0.075);
    }}

    /* ---------- 按钮 ---------- */
    QPushButton {{
        background-color: rgba(255, 255, 255, 0.07);
        border: 1px solid {c['border']};
        border-radius: 18px;
        padding: 8px 18px;
        color: {c['text']};
    }}
    QPushButton:hover {{ background-color: {c['card_hover']}; }}
    QPushButton:pressed {{ background-color: rgba(255, 255, 255, 0.11); }}
    QPushButton:disabled {{ color: {c['text_faint']}; }}

    QPushButton#Primary {{
        background-color: {c['accent_deep']};
        border: 1px solid rgba(94, 234, 212, 0.5);
        color: #04211f;
        font-weight: 600;
    }}
    QPushButton#Primary:hover {{ background-color: #10b8b4; }}
    QPushButton#Primary:disabled {{ background-color: rgba(94, 234, 212, 0.2); color: #5f7b78; }}

    QPushButton#Ghost {{
        background: transparent;
        border: 1px solid {c['border']};
        color: {c['text_dim']};
        border-radius: 16px;
        padding: 6px 14px;
        font-size: 12px;
    }}
    QPushButton#Ghost:hover {{ color: {c['text']}; background-color: {c['card']}; }}

    /* ---------- 气泡上的小喇叭 ---------- */
    QPushButton#SpeakBtn {{
        background: transparent;
        border: none;
        padding: 0;
        font-size: 13px;
        color: {c['text_faint']};
    }}
    QPushButton#SpeakBtn:hover {{
        background-color: rgba(255, 255, 255, 0.10);
        border-radius: 13px;
        color: {c['text']};
    }}
    QPushButton#SpeakBtn[playing="true"] {{
        background-color: rgba(94, 234, 212, 0.22);
        border-radius: 13px;
        color: {c['accent']};
    }}

    /* ---------- 底部"说话"按钮：聆听中变红 ---------- */
    QPushButton#Mic[active="true"] {{
        background-color: rgba(248, 113, 113, 0.18);
        border: 1px solid rgba(248, 113, 113, 0.55);
        color: #fecaca;
    }}

    /* ---------- 聊天区 ---------- */
    QScrollArea#ChatScroll {{
        background: transparent;
        border: none;
    }}
    QScrollArea#ChatScroll > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 4px 0;
    }}
    QScrollBar::handle:vertical {{
        background: rgba(255, 255, 255, 0.14);
        border-radius: 4px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: rgba(255, 255, 255, 0.24); }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

    /* ---------- 其它 ---------- */
    QProgressBar#Memory {{
        background-color: rgba(255, 255, 255, 0.06);
        border: none;
        border-radius: 4px;
        height: 6px;
        text-align: center;
    }}
    QProgressBar#Memory::chunk {{
        background-color: {c['accent_deep']};
        border-radius: 4px;
    }}
    QCheckBox {{ color: {c['text_dim']}; spacing: 8px; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px;
        border-radius: 5px;
        border: 1px solid {c['border']};
        background: rgba(255,255,255,0.05);
    }}
    QCheckBox::indicator:checked {{
        background: {c['accent_deep']};
        border: 1px solid {c['accent']};
    }}
    QDialog {{
        background-color: #121830;
    }}
    QComboBox, QSpinBox, QLineEdit {{
        background-color: rgba(255, 255, 255, 0.06);
        border: 1px solid {c['border']};
        border-radius: 8px;
        padding: 6px 10px;
    }}
    QComboBox QAbstractItemView {{
        background-color: #1a2140;
        border: 1px solid {c['border']};
        selection-background-color: rgba(94, 234, 212, 0.18);
    }}
    """
