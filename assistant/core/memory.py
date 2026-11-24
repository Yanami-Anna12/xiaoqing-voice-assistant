"""会话记忆。

题目要求"能记住最近 3 轮对话"：这里用一个定长队列保存最近 N 轮，
每轮 = 用户一句话 + 助手一句话，拼进请求时按时间顺序还原成 messages。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class Round:
    user: str
    assistant: str


class ConversationMemory:
    def __init__(self, rounds: int = 3):
        self.capacity = max(1, int(rounds))
        self._rounds: deque[Round] = deque(maxlen=self.capacity)

    # ---------- 写 ----------
    def add(self, user: str, assistant: str) -> None:
        if not user.strip():
            return
        self._rounds.append(Round(user.strip(), (assistant or "").strip()))

    def clear(self) -> None:
        self._rounds.clear()

    # ---------- 读 ----------
    @property
    def used(self) -> int:
        return len(self._rounds)

    @property
    def capacity_rounds(self) -> int:
        return self.capacity

    def as_messages(self) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for r in self._rounds:
            messages.append({"role": "user", "content": r.user})
            if r.assistant:
                messages.append({"role": "assistant", "content": r.assistant})
        return messages

    def build_messages(self, system_prompt: str, user_text: str) -> list[dict[str, str]]:
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(self.as_messages())
        messages.append({"role": "user", "content": user_text})
        return messages

    def brief(self) -> str:
        """给界面/日志用的一句话摘要。"""
        if not self._rounds:
            return "暂无记忆"
        return " / ".join(r.user[:12] for r in self._rounds)
