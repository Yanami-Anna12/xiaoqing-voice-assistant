"""唤醒词判定。

方案：把"唤醒"当成一次很短的语音识别——听到一句短话，识别成文字，
再判断里面有没有名字。优点是零额外模型、可以随手改唤醒词；
小模型偶尔把名字听岔，所以配了别名 + 模糊匹配两层兜底。
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from ..config import WakeWordConfig

_PUNCT_RE = re.compile(r"[\s,，。.!！?？、~～…·:：;；\"'“”‘’()（）\[\]【】]+")
_WAKE_PREFIXES = ("你好", "嗨", "嘿", "hi", "hello", "喂")


def normalize(text: str) -> str:
    return _PUNCT_RE.sub("", (text or "").lower())


@dataclass
class WakeResult:
    woken: bool
    remainder: str = ""  # 唤醒词后面紧跟的指令（"小清，今天天气"这种一句话唤醒）
    matched: str = ""  # 实际命中的是哪个别名，方便答辩时展示


class WakeWordDetector:
    def __init__(self, cfg: WakeWordConfig):
        self.cfg = cfg
        self.aliases = [a for a in [cfg.word, *cfg.aliases] if a]

    # ---------- 对外 ----------
    def match(self, text: str) -> WakeResult:
        norm = normalize(text)
        if not norm:
            return WakeResult(False)

        for alias in self.aliases:
            idx = norm.find(normalize(alias))
            if idx >= 0:
                remainder = norm[idx + len(normalize(alias)):]
                return WakeResult(True, remainder.strip(), alias)

        hit = self._fuzzy(norm)
        if hit:
            word = normalize(hit)
            idx = norm.find(word)
            remainder = norm[idx + len(word):] if idx >= 0 else ""
            return WakeResult(True, remainder.strip(), hit)
        return WakeResult(False)

    def strip_prefix(self, text: str) -> str:
        """把开头的"你好""嗨"去掉，避免"你好小清"识别成"你好小亲"匹配不上。"""
        norm = normalize(text)
        for prefix in _WAKE_PREFIXES:
            if norm.startswith(prefix):
                return norm[len(prefix):]
        return norm

    # ---------- 内部 ----------
    def _fuzzy(self, norm: str) -> str:
        """在句子里滑窗找与唤醒词最像的片段（名字听岔时兜底）。

        两个字的名字（小清/小明）不做模糊匹配：两字之间只差一个字就是 0.5 相似度，
        "小心""小麦"这种日常词会天天误唤醒。这类情况交给 aliases 显式覆盖。
        """
        target = normalize(self.cfg.word)
        if len(target) < 3:
            return ""
        best, best_ratio = "", 0.0
        for size in {len(target), len(target) + 1}:
            if size > len(norm):
                continue
            for i in range(len(norm) - size + 1):
                seg = norm[i:i + size]
                # 锚定首字，避免把"小明""小麦"也当唤醒
                if seg[0] != target[0]:
                    continue
                ratio = difflib.SequenceMatcher(None, seg, target).ratio()
                if ratio > best_ratio:
                    best, best_ratio = seg, ratio
        if best_ratio >= self.cfg.fuzzy_threshold:
            return best
        return ""
