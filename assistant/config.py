"""全局配置。

所有可调参数集中在这里：
- 默认值写在 dataclass 里；
- 用户改动保存到 data/config.json（界面里的"设置"会写这个文件）；
- 启动时如果存在 config.json，就以它为覆盖。

这样"唤醒词、模型地址、记忆轮数"都不用改代码。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = DATA_DIR / "logs"
CONFIG_FILE = DATA_DIR / "config.json"
MODELS_DIR = PROJECT_ROOT / "models"

DEFAULT_SYSTEM_PROMPT = (
    "你是一个叫「小清」的桌面语音助手，说话自然、口语化、亲切。"
    "回答要短，一般不超过三句话，适合直接朗读出来，不要使用 Markdown 符号。"
    "需要实时信息（天气、时间等）时，调用工具获取后再回答，不要编造数据。"
    "你记得之前几轮聊过什么，可以自然地承接上下文。"
)


def _registry_env(name: str) -> str:
    """Windows 注册表里的环境变量。

    有些启动方式（从旧进程、IDE、快捷方式启动）拿不到后来才设置的环境变量，
    os.getenv 会读空；这里直接查注册表兜底，免得用户以为"配了 Key 却不生效"。
    """
    if os.name != "nt":
        return ""
    try:
        import winreg
    except ImportError:  # pragma: no cover - 非 Windows
        return ""
    locations = (
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    )
    for root, path in locations:
        try:
            with winreg.OpenKey(root, path) as key:
                value, _ = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        if value:
            return str(value)
    return ""


def read_env(name: str, default: str = "") -> str:
    """进程环境变量 → 注册表 → 默认值。"""
    return (os.getenv(name) or _registry_env(name) or default).strip()


def _default_aliases() -> list[str]:
    """常见谐音/识别偏差，小模型容易把"小清"听成这些音。"""
    return [
        "小清", "小晴", "小请", "小琴", "小青", "小庆", "小卿",
        "晓清", "晓晴", "小蜻", "小轻", "小倾", "小亲",
    ]


@dataclass
class WakeWordConfig:
    """唤醒设置。"""

    word: str = "小清"
    aliases: list[str] = field(default_factory=_default_aliases)
    mode: str = "voice"  # voice=喊名字唤醒；push=按按钮唤醒（安静的考试环境用）
    fuzzy_threshold: float = 0.5  # 名字被听岔时的模糊匹配阈值（0~1，越低越容易唤醒）
    followup_timeout_ms: int = 6000  # 唤醒后等指令的时间


@dataclass
class ASRConfig:
    """语音识别设置。"""

    backend: str = "vosk"  # vosk | mock
    model_path: str = str(MODELS_DIR / "vosk-model-small-cn-0.22")
    sample_rate: int = 16000
    silence_ms: int = 800  # 说完多久判定为一句结束
    silence_start_dbfs: float = -38.0  # VAD：开始说话的阈值
    silence_continue_dbfs: float = -46.0  # VAD：说话过程中的阈值（滞回）
    max_utterance_ms: int = 12000  # 单句最长拾音时间
    min_utterance_ms: int = 300  # 太短的噪声直接丢弃


@dataclass
class LLMConfig:
    """大模型设置（OpenAI 兼容协议，默认 DeepSeek）。"""

    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    api_key: str = ""  # 也可以不填，自动读环境变量
    api_key_env: str = "DEEPSEEK_API_KEY"
    temperature: float = 0.6
    memory_rounds: int = 3  # 题目要求：记住最近 3 轮对话
    max_tool_steps: int = 3
    timeout_s: float = 30.0
    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    def resolve_api_key(self) -> str:
        return self.api_key.strip() or read_env(self.api_key_env)


@dataclass
class TTSConfig:
    """语音合成设置。"""

    backend: str = "auto"  # auto | edge | sapi | mock
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: int = 0  # 语速百分比，-50 ~ +50
    enabled: bool = True


@dataclass
class ToolConfig:
    """工具设置。"""

    weather_enabled: bool = True
    default_city: str = "北京"


@dataclass
class UIConfig:
    """界面设置。"""

    width: int = 1080
    height: int = 720
    font_size: int = 14


@dataclass
class AppConfig:
    wake: WakeWordConfig = field(default_factory=WakeWordConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    tools: ToolConfig = field(default_factory=ToolConfig)
    ui: UIConfig = field(default_factory=UIConfig)

    # ---------- 读写 ----------
    @classmethod
    def load(cls, path: Path | None = None) -> AppConfig:
        path = path or CONFIG_FILE
        cfg = cls()
        if not path.exists():
            return cfg
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cfg
        for section in fields(cls):
            value = raw.get(section.name)
            if not isinstance(value, dict):
                continue
            target = getattr(cfg, section.name)
            valid = {f.name for f in fields(target)}
            for key, val in value.items():
                if key in valid:
                    setattr(target, key, val)
        return cfg

    def save(self, path: Path | None = None) -> Path:
        path = path or CONFIG_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = asdict(self)
        # data/ 已在 .gitignore 里，Key 只留在本机，不会进仓库
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


_config: AppConfig | None = None


def get_config() -> AppConfig:
    """进程内单例，避免各处重复读文件。"""
    global _config
    if _config is None:
        _config = AppConfig.load()
    return _config


def reload_config() -> AppConfig:
    global _config
    _config = AppConfig.load()
    return _config
