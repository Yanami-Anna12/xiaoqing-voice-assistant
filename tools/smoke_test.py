"""自检脚本：不开界面，验证每一层都能独立工作。

    python tools/smoke_test.py

覆盖：配置、唤醒匹配、记忆、工具、Agent 工具循环、Vosk 加载。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from assistant.config import AppConfig  # noqa: E402
from assistant.core.agent import AssistantAgent  # noqa: E402
from assistant.core.llm import MockLLM  # noqa: E402
from assistant.core.memory import ConversationMemory  # noqa: E402
from assistant.core.tools import build_default_registry, calculate  # noqa: E402
from assistant.core.wakeword import WakeWordDetector  # noqa: E402

PASSED, FAILED = 0, 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  [PASS] {name} {detail}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name} {detail}")


def test_wake() -> None:
    print("\n[1] 唤醒词匹配")
    cfg = AppConfig()
    detector = WakeWordDetector(cfg.wake)
    cases = [
        ("小清", True),
        ("小清，今天天气怎么样", True),
        ("你好小晴", True),
        ("晓晴在吗", True),
        ("今天天气不错", False),
        ("明天要开会", False),
    ]
    for text, expected in cases:
        result = detector.match(text)
        check(f"「{text}」", result.woken == expected, f"woken={result.woken} matched={result.matched!r}")
    result = detector.match("小清，今天天气怎么样")
    check("一句话唤醒+指令", result.remainder == "今天天气怎么样", repr(result.remainder))


def test_memory() -> None:
    print("\n[2] 三轮记忆")
    memory = ConversationMemory(rounds=3)
    for i in range(5):
        memory.add(f"问题{i}", f"回答{i}")
    check("只保留最近3轮", memory.used == 3, f"used={memory.used}")
    messages = memory.build_messages("system", "当前问题")
    check("消息条数 = system + 3轮*2 + 当前", len(messages) == 8, f"len={len(messages)}")
    check("最早的一轮被挤掉", messages[1]["content"] == "问题2", messages[1]["content"])


def test_tools() -> None:
    print("\n[3] 工具")
    check("计算器 128*7", calculate("128*7") == "128*7 = 896", calculate("128*7"))
    check("计算器 非法输入不崩", "算不出来" in calculate("__import__('os')"), calculate("__import__('os')"))
    registry = build_default_registry(AppConfig().tools)
    check("工具已注册", {"get_weather", "get_current_time", "calculate"} <= set(registry.names()), str(registry.names()))
    t0 = time.perf_counter()
    try:
        weather = registry.call("get_weather", {"city": "北京"})
        ok = "北京" in weather and "摄氏度" in weather
        check("天气工具联网", ok, f"{(time.perf_counter() - t0):.1f}s {weather[:60]}")
    except Exception as exc:  # noqa: BLE001
        check("天气工具联网", False, f"异常：{exc}")
    check("时间工具", "星期" in registry.call("get_current_time", {}), "")


def test_agent() -> None:
    print("\n[4] Agent：工具调用 + 记忆")
    cfg = AppConfig()
    cfg.tools.default_city = "北京"
    llm = MockLLM(cfg.llm, default_city="北京")
    memory = ConversationMemory(cfg.llm.memory_rounds)
    agent = AssistantAgent(cfg, llm, build_default_registry(cfg.tools), memory)

    first = agent.respond("今天北京天气怎么样")
    check("模型主动调天气工具", len(first.trace) == 1 and first.trace[0].name == "get_weather", str([t.name for t in first.trace]))
    check("工具结果进了回复", "北京" in first.text, first.text[:60])

    agent.respond("我叫阿伟")
    last = agent.respond("你还记得我叫什么吗")
    check("记得上一轮的名字", "阿伟" in last.text, last.text[:60])
    check("记忆已满 3 轮", memory.used == 3, f"used={memory.used}")

    calc = agent.respond("帮我算一下 128 乘以 7")
    check("算术走工具", calc.trace and "896" in calc.text, calc.text[:60])


def test_vad() -> None:
    print("\n[5] VAD 切句")
    import numpy as np

    from assistant.core.audio import UtteranceSegmenter

    sr, block = 16000, 480
    segmenter = UtteranceSegmenter(
        sample_rate=sr, block_ms=30, start_dbfs=-38, continue_dbfs=-46,
        silence_ms=800, max_ms=12000, min_ms=300,
    )
    silence = np.zeros(block, dtype=np.int16).tobytes()
    timeline = np.arange(block) / sr
    tone = (np.sin(2 * np.pi * 440 * timeline) * 8000).astype(np.int16).tobytes()

    captured = None
    for _ in range(10):
        captured = segmenter.feed(silence) or captured
    for _ in range(33):
        captured = segmenter.feed(tone) or captured
    for _ in range(34):
        captured = segmenter.feed(silence) or captured
    seconds = len(captured) / 2 / sr if captured else 0
    check("一句话能被切出来", captured is not None and 0.5 < seconds < 4, f"{seconds:.2f}s")
    check("切完之后缓冲区已清空", not segmenter.active, "")


def test_tts() -> None:
    print("\n[6] TTS 后端")
    from assistant.core.tts import create_tts

    client = create_tts(AppConfig().tts)
    check("有可用音色", client.name != "静默模式（无语音输出）", client.name)
    client.close()


def test_vosk() -> None:
    print("\n[7] Vosk 模型加载")
    cfg = AppConfig()
    model_dir = Path(cfg.asr.model_path)
    if not model_dir.exists():
        check("模型目录", False, f"缺少 {model_dir}（先运行 scripts/download_model.ps1）")
        return
    from assistant.core.asr import VoskASR

    t0 = time.perf_counter()
    asr = VoskASR(str(model_dir))
    check("加载模型", True, f"{time.perf_counter() - t0:.1f}s")
    silent = b"\x00\x00" * 16000
    check("静音返回空串", asr.transcribe(silent) == "", repr(asr.transcribe(silent))[:40])
    asr.close()


def main() -> int:
    print("=" * 64)
    print("小清语音助手 · 模块自检")
    print("=" * 64)
    test_wake()
    test_memory()
    test_tools()
    test_agent()
    test_vad()
    test_tts()
    test_vosk()
    print("\n" + "=" * 64)
    print(f"结果：{PASSED} 项通过，{FAILED} 项失败")
    print("=" * 64)
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
