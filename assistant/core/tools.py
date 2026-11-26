"""工具（Function Calling）层。

模型自己决定要不要调工具，这里只负责：
1. 把工具描述成 OpenAI 兼容的 JSON Schema，交给模型；
2. 模型返回 tool_calls 时真正执行，把结果回到模型。
新增工具 = 写一个函数 + 注册，不动模型和界面。
"""
from __future__ import annotations

import ast
import json
import operator
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

from ..config import ToolConfig
from ..logging_setup import get_logger

log = get_logger(__name__)

# WMO 天气代码 -> 中文
_WEATHER_CODES = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴",
    45: "有雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "强阵雨", 82: "暴雨",
    85: "阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷阵雨伴冰雹",
}


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., str]
    enabled: bool = True

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values() if t.enabled]

    def names(self) -> list[str]:
        return [t.name for t in self._tools.values() if t.enabled]

    def call(self, name: str, arguments: dict[str, Any] | str) -> str:
        spec = self._tools.get(name)
        if spec is None:
            return f"（没有叫 {name} 的工具）"
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
        started = time.perf_counter()
        try:
            result = spec.handler(**arguments)
        except TypeError as exc:
            result = f"（参数不对：{exc}）"
        except Exception as exc:  # noqa: BLE001 - 工具失败不能让对话崩掉
            log.exception("工具 %s 执行失败", name)
            result = f"（工具执行失败：{exc}）"
        log.info("工具 %s(%s) -> %s（%.0fms）", name, arguments, result, (time.perf_counter() - started) * 1000)
        return result


# ---------------------------------------------------------------- 天气
_WEATHER_CACHE: dict[str, tuple[float, str]] = {}
_GEO_CACHE: dict[str, tuple[float, float, str]] = {}
_CACHE_TTL = 300  # 秒


def _weather_desc(code: int | None) -> str:
    return _WEATHER_CODES.get(int(code or 0), "未知天气")


def get_weather(city: str = "") -> str:
    """Open-Meteo 免费接口，不需要 API Key。"""
    city = (city or "").strip() or "北京"
    cached = _WEATHER_CACHE.get(city)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]

    headers = {"User-Agent": "voice-assistant/1.0"}
    cached_geo = _GEO_CACHE.get(city)
    if cached_geo is not None:
        lat, lon, place_name = cached_geo
    else:
        geo = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "zh", "format": "json"},
            timeout=6,
            headers=headers,
        ).json()
        results = geo.get("results") or []
        if not results:
            return f"没有找到{city}的位置信息，换个大一点的城市名试试。"
        place = results[0]
        lat, lon = place["latitude"], place["longitude"]
        place_name = str(place.get("name", city))
        admin = str(place.get("admin1") or "")
        # 避免"北京市北京"这种重复
        if admin and admin not in place_name and place_name not in admin:
            place_name = f"{admin}{place_name}"
        _GEO_CACHE[city] = (lat, lon, place_name)  # 同一个城市不用每次都地理编码

    data = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
            "timezone": "auto",
        },
        timeout=6,
        headers=headers,
    ).json()
    current = data.get("current") or {}
    text = (
        f"{place_name}现在{_weather_desc(current.get('weather_code'))}，"
        f"气温{current.get('temperature_2m', '?')}摄氏度，"
        f"体感{current.get('apparent_temperature', '?')}摄氏度，"
        f"湿度{current.get('relative_humidity_2m', '?')}%，"
        f"风速{current.get('wind_speed_10m', '?')}公里每小时。"
    )
    _WEATHER_CACHE[city] = (time.time(), text)
    return text


def get_current_time(city: str = "本地") -> str:  # city 参数让模型调用时不必改 schema
    now = datetime.now()
    weekday = "一二三四五六日"[now.weekday()]
    return f"现在是{now:%Y年%m月%d日 %H点%M分}，星期{weekday}。"


# ---------------------------------------------------------------- 计算器
_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("只支持数字和加减乘除运算")


def calculate(expression: str) -> str:
    expression = (expression or "").replace("×", "*").replace("÷", "/").replace("^", "**")
    try:
        tree = ast.parse(expression, mode="eval")
        value = _eval_node(tree)
    except Exception as exc:  # noqa: BLE001
        return f"（算不出来：{exc}）"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{expression.strip()} = {value}"


# ---------------------------------------------------------------- 注册
def build_default_registry(cfg: ToolConfig) -> ToolRegistry:
    registry = ToolRegistry()
    if cfg.weather_enabled:
        registry.register(
            ToolSpec(
                name="get_weather",
                description="查询某个城市当前的实时天气。用户问天气、温度、下雨、穿什么衣服时调用。",
                parameters={
                    "type": "object",
                    "properties": {
                        "city": {
                            "type": "string",
                            "description": f"城市中文名，例如 郑州、北京。用户没说城市时用 {cfg.default_city}。",
                        }
                    },
                    "required": [],
                },
                handler=lambda city=cfg.default_city: get_weather(city),
            )
        )
    registry.register(
        ToolSpec(
            name="get_current_time",
            description="获取本机当前日期和时间。用户问几点、今天几号、星期几时调用。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=get_current_time,
        )
    )
    registry.register(
        ToolSpec(
            name="calculate",
            description="计算数学表达式，例如 128*7、(3+5)/2。用户让你算数时调用。",
            parameters={
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "只含数字和 + - * / ( ) ** 的表达式"}
                },
                "required": ["expression"],
            },
            handler=calculate,
        )
    )
    return registry
