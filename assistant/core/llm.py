"""大模型层（OpenAI 兼容协议，默认 DeepSeek）。

只做两件事：发 messages 拿回复、把 tool_calls 结构化地交回上层。
对话记忆、工具执行循环在 agent.py，模型层不掺业务。
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ..config import LLMConfig
from ..logging_setup import get_logger

log = get_logger(__name__)


class LLMError(RuntimeError):
    """给用户看的错误，消息本身就是中文提示。"""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str = "{}"

    @property
    def parsed(self) -> dict[str, Any]:
        try:
            data = json.loads(self.arguments or "{}")
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""

    @property
    def wants_tool(self) -> bool:
        return bool(self.tool_calls)

    def as_message(self) -> dict[str, Any]:
        """还原成 OpenAI 协议的 assistant 消息，便于把工具结果接回去。"""
        message: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": c.arguments},
                }
                for c in self.tool_calls
            ]
        return message


class BaseLLM(ABC):
    name: str = "unknown"
    model: str = ""

    @abstractmethod
    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> LLMResponse:
        ...


class DeepSeekClient(BaseLLM):
    name = "DeepSeek"

    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        self.model = cfg.model
        key = cfg.resolve_api_key()
        if not key:
            raise LLMError(
                f"没有配置 API Key：请在设置里填写，或设置环境变量 {cfg.api_key_env}"
            )
        try:
            from openai import OpenAI
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"openai 库不可用：{exc}") from exc
        self._client = OpenAI(api_key=key, base_url=cfg.base_url, timeout=cfg.timeout_s)

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> LLMResponse:
        from openai import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError, RateLimitError

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.cfg.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except AuthenticationError as exc:
            raise LLMError("API Key 无效或已过期，请到设置里换一个") from exc
        except RateLimitError as exc:
            raise LLMError("请求太频繁或余额不足，稍后再试") from exc
        except APITimeoutError as exc:
            raise LLMError("模型响应超时，请检查网络") from exc
        except APIConnectionError as exc:
            raise LLMError("连不上模型服务，请检查网络或 base_url") from exc
        except APIStatusError as exc:
            raise LLMError(f"模型服务返回错误 {exc.status_code}") from exc

        choice = resp.choices[0].message
        calls = [
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=call.function.arguments or "{}",
            )
            for call in (choice.tool_calls or [])
        ]
        return LLMResponse(content=(choice.content or "").strip(), tool_calls=calls, model=resp.model)


class MockLLM(BaseLLM):
    """没有 Key 时的离线兜底。

    它当然不是真模型，但把"记忆 + 工具调用"的链路真实跑了一遍，
    界面上会明确标注"演示模型"，不会让人误以为是 DeepSeek 的回复。
    """

    name = "离线演示模型"

    _CITIES: ClassVar[list[str]] = [
        "北京", "上海", "广州", "深圳", "郑州", "杭州", "成都", "武汉", "西安",
        "南京", "天津", "重庆", "苏州", "长沙", "青岛", "大连", "厦门", "合肥", "济南",
    ]

    def __init__(self, cfg: LLMConfig, default_city: str = "北京"):
        self.cfg = cfg
        self.default_city = default_city
        self.model = "mock"

    # ---------- 主入口 ----------
    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> LLMResponse:
        user_text = self._last_user(messages)
        last_tool = self._last_tool(messages)
        tool_names = {t["function"]["name"] for t in (tools or [])}

        if last_tool is not None:
            return LLMResponse(content=self._wrap_tool_result(*last_tool), model=self.model)

        if user_text and "get_weather" in tool_names and re.search(r"天气|气温|下雨|冷不冷|热不热|穿什么", user_text):
            city = self._extract_city(user_text)
            return LLMResponse(
                tool_calls=[ToolCall(id="call_weather", name="get_weather", arguments=json.dumps({"city": city}, ensure_ascii=False))],
                model=self.model,
            )

        if user_text and "get_current_time" in tool_names and re.search(r"几点|时间|几号|星期|日期", user_text):
            return LLMResponse(tool_calls=[ToolCall(id="call_time", name="get_current_time", arguments="{}")], model=self.model)

        if user_text and "calculate" in tool_names and re.search(r"算一下|计算|等于|乘以|除以|加上|减去", user_text):
            expr = user_text
            expr = expr.replace("乘以", "*").replace("除以", "/").replace("加上", "+").replace("减去", "-")
            expr = expr.replace("算一下", "").replace("计算", "").replace("等于多少", "").replace("等于", "")
            matched = re.search(r"[0-9\.\+\-\*/\(\)\s]+", expr)
            if matched and any(ch.isdigit() for ch in matched.group()):
                return LLMResponse(
                    tool_calls=[ToolCall(id="call_calc", name="calculate", arguments=json.dumps({"expression": matched.group().strip()}, ensure_ascii=False))],
                    model=self.model,
                )

        return LLMResponse(content=self._small_talk(user_text, messages), model=self.model)

    # ---------- 小工具 ----------
    @staticmethod
    def _last_user(messages: list[dict[str, Any]]) -> str:
        for msg in reversed(messages):
            if msg.get("role") == "user":
                return str(msg.get("content") or "")
        return ""

    @staticmethod
    def _last_tool(messages: list[dict[str, Any]]) -> tuple[str, str] | None:
        """找本轮最近的工具结果，返回 (工具名, 结果文本)。"""
        name_by_id: dict[str, str] = {}
        last: tuple[str, str] | None = None
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for call in msg["tool_calls"]:
                    name_by_id[call.get("id", "")] = call.get("function", {}).get("name", "")
            elif msg.get("role") == "tool":
                name = name_by_id.get(msg.get("tool_call_id", ""), "")
                last = (name, str(msg.get("content") or ""))
        return last

    @staticmethod
    def _wrap_tool_result(name: str, result: str) -> str:
        if name == "calculate":
            return f"算出来是：{result.split('=')[-1].strip() if '=' in result else result}"
        if name == "get_current_time":
            return result
        return f"我查了一下，{result}"

    def _extract_city(self, text: str) -> str:
        for city in self._CITIES:
            if city in text:
                return city
        return self.default_city

    def _small_talk(self, text: str, messages: list[dict[str, Any]]) -> str:
        if not text:
            return "我在，你说。"
        if re.search(r"我叫什么|我是谁|记得我", text):
            for msg in messages:
                content = str(msg.get("content") or "")
                matched = re.search(r"我叫([\u4e00-\u9fa5A-Za-z]{1,8})", content)
                if matched and msg.get("role") == "user":
                    return f"你刚才说你叫{matched.group(1)}，我记着呢。"
            return "你还没告诉我你叫什么呀。"
        matched = re.search(r"我叫([\u4e00-\u9fa5A-Za-z]{1,8})", text)
        if matched:
            return f"你好，{matched.group(1)}，很高兴认识你。"
        if re.search(r"你好|在吗|hi|hello|嗨", text, re.I):
            return "我在呢，随时可以让小清帮你查天气、算数、聊两句。"
        if re.search(r"谢谢|辛苦", text):
            return "不客气，有事随时叫我。"
        if re.search(r"再见|拜拜", text):
            return "拜拜，记得喊我小清。"
        return f"我听到你说「{text}」。现在没连大模型，先陪你聊到这，配好 API Key 我就能更聪明地回答了。"
