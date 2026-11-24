"""对话编排：记忆 + 大模型 + 工具循环。

这一层不碰音频、不碰 Qt，只接收一句文本，返回"答案 + 工具调用轨迹"。
好处是：换成语音、换成打字、写单元测试，都复用同一套逻辑。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..config import AppConfig
from ..logging_setup import get_logger
from .llm import BaseLLM, LLMError, LLMResponse, ToolCall
from .memory import ConversationMemory
from .tools import ToolRegistry

log = get_logger(__name__)


@dataclass
class ToolTrace:
    name: str
    arguments: dict
    result: str

    def summary(self) -> str:
        args = "，".join(f"{k}={v}" for k, v in self.arguments.items()) or "无参数"
        return f"{self.name}({args}) → {self.result}"


@dataclass
class TurnResult:
    text: str
    trace: list[ToolTrace] = field(default_factory=list)
    latency_ms: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


class AssistantAgent:
    def __init__(self, cfg: AppConfig, llm: BaseLLM, tools: ToolRegistry, memory: ConversationMemory):
        self.cfg = cfg
        self.llm = llm
        self.tools = tools
        self.memory = memory

    @property
    def model_name(self) -> str:
        return f"{self.llm.name} · {self.llm.model}"

    def new_session(self) -> None:
        self.memory.clear()

    # ------------------------------------------------------------ 主流程
    def respond(self, text: str) -> TurnResult:
        text = (text or "").strip()
        started = time.perf_counter()
        if not text:
            return TurnResult("我没听清，再说一遍好吗？", latency_ms=0.0)

        messages = self.memory.build_messages(self.cfg.llm.system_prompt, text)
        trace: list[ToolTrace] = []
        answer = ""

        try:
            answer = self._run_tool_loop(messages, trace)
        except LLMError as exc:
            log.warning("大模型调用失败：%s", exc)
            return TurnResult(
                text="模型这会儿没连上，我先记下你的问题，等一下再试。",
                trace=trace,
                latency_ms=(time.perf_counter() - started) * 1000,
                error=str(exc),
            )

        answer = answer or "我在，没听明白，你再说一遍？"
        self.memory.add(text, answer)
        return TurnResult(
            text=answer,
            trace=trace,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    # ------------------------------------------------------------ 工具循环
    def _run_tool_loop(self, messages: list[dict], trace: list[ToolTrace]) -> str:
        """模型要调工具就执行、把结果喂回去，最多 max_tool_steps 轮。"""
        schemas = self.tools.schemas()
        for step in range(max(1, self.cfg.llm.max_tool_steps)):
            response: LLMResponse = self.llm.complete(messages, schemas)
            if not response.wants_tool:
                return response.content.strip()

            log.info("第 %d 轮工具调用：%s", step + 1, [c.name for c in response.tool_calls])
            messages.append(response.as_message())
            for call in response.tool_calls:
                self._execute_tool(call, messages, trace)

        # 防死循环：最后一次强制不许调工具，逼模型给结论
        final = self.llm.complete(messages, None)
        return final.content.strip() or "我查了几次还没查到，换个问法试试？"

    def _execute_tool(self, call: ToolCall, messages: list[dict], trace: list[ToolTrace]) -> None:
        result = self.tools.call(call.name, call.parsed)
        trace.append(ToolTrace(call.name, call.parsed, result))
        messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
