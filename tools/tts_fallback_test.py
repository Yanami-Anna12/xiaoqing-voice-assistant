"""TTS 降级自检：在线后端挂了必须换离线语音，而不是悄悄静音。

    python tools/tts_fallback_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from assistant.config import TTSConfig  # noqa: E402
from assistant.core.tts import TTSBackend, TTSClient, TTSError  # noqa: E402


class BadBackend(TTSBackend):
    name = "坏后端（模拟掉线）"

    def __init__(self) -> None:
        self.calls = 0

    def speak(self, text: str) -> None:
        self.calls += 1
        raise TTSError("模拟网络失败")


class GoodBackend(TTSBackend):
    name = "好后端"

    def __init__(self) -> None:
        self.texts: list[str] = []

    def speak(self, text: str) -> None:
        self.texts.append(text)


def main() -> int:
    notices: list[str] = []

    client = TTSClient(TTSConfig(backend="mock"))
    bad, good = BadBackend(), GoodBackend()
    client.enabled = True
    client.backends = [bad, good]  # type: ignore[list-item]
    client.on_notice = notices.append
    client.speak("第一句")

    checks = [
        ("失败后端被跳过，换到好后端", good.texts == ["第一句"]),
        ("失败后端只尝试一次", bad.calls == 1),
        ("降级有提示", any("切换" in n for n in notices)),
    ]

    client.speak("第二句")
    checks.append(("冷却期内不再碰坏后端", bad.calls == 1 and good.texts == ["第一句", "第二句"]))

    # 全部失败：落到静默，但不能崩
    silent = TTSClient(TTSConfig(backend="mock"))
    silent.enabled = True
    silent.backends = [BadBackend()]  # type: ignore[list-item]
    silent_notices: list[str] = []
    silent.on_notice = silent_notices.append
    crashed = False
    try:
        silent.speak("第三句")
    except Exception:  # noqa: BLE001
        crashed = True
    checks.append(("全部失败不崩且提示静音", not crashed and any("静音" in n or "语音合成" in n for n in silent_notices)))

    failed = 0
    for name, ok in checks:
        print(("  [PASS] " if ok else "  [FAIL] ") + name)
        failed += 0 if ok else 1
    print(f"\n结果：{'PASS' if failed == 0 else 'FAIL'}（{len(checks) - failed}/{len(checks)}）")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
