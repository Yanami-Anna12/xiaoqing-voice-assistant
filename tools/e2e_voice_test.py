"""端到端语音自检：合成中文语音 → ffmpeg 转 16k PCM → Vosk 识别 → 唤醒判定。

不需要麦克风，也不需要人说话，验证的是"真正的声音"能不能走通识别链路。
需要：edge-tts（联网）+ ffmpeg/ffplay + 已下载 Vosk 模型。

    python tools/e2e_voice_test.py
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from assistant.config import AppConfig  # noqa: E402
from assistant.core.agent import AssistantAgent  # noqa: E402
from assistant.core.asr import VoskASR  # noqa: E402
from assistant.core.memory import ConversationMemory  # noqa: E402
from assistant.core.pipeline import build_llm  # noqa: E402
from assistant.core.tools import build_default_registry  # noqa: E402
from assistant.core.wakeword import WakeWordDetector  # noqa: E402


def synth_mp3(text: str, out: Path) -> None:
    import edge_tts

    async def run() -> None:
        await edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural").save(str(out))

    asyncio.run(run())


def mp3_to_pcm16k(mp3: Path) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("找不到 ffmpeg")
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as tmp:
        raw_path = Path(tmp.name)
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(mp3), "-ar", "16000", "-ac", "1", "-f", "s16le", str(raw_path)],
        check=True,
    )
    data = raw_path.read_bytes()
    raw_path.unlink(missing_ok=True)
    return data


def main() -> int:
    cfg = AppConfig()
    model_dir = Path(cfg.asr.model_path)
    if not model_dir.exists():
        print(f"[FAIL] 缺少模型：{model_dir}")
        return 1

    print("[1/3] 加载 Vosk 模型…")
    asr = VoskASR(str(model_dir))
    detector = WakeWordDetector(cfg.wake)
    llm, note = build_llm(cfg)
    print(f"      模型后端：{llm.name}（{note}）")
    agent = AssistantAgent(cfg, llm, build_default_registry(cfg.tools), ConversationMemory(cfg.llm.memory_rounds))

    cases = [
        "小清，今天北京天气怎么样",
        "你好小清，现在几点了",
        "今天天气不错",
    ]
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for i, text in enumerate(cases, 1):
            mp3 = Path(tmp) / f"case{i}.mp3"
            print(f"\n[2/3] 合成并识别：{text}")
            synth_mp3(text, mp3)
            pcm = mp3_to_pcm16k(mp3)
            result = asr.transcribe(pcm, 16000)
            print(f"      识别结果：{result!r}（音频 {len(pcm) / 2 / 16000:.1f}s）")
            woken = detector.match(result)
            should_wake = text.startswith(("小清", "你好小清"))
            ok = bool(result) and (woken.woken == should_wake)
            print(f"      唤醒={woken.woken} 期望={should_wake} → {'PASS' if ok else 'FAIL'}")
            failed += 0 if ok else 1

            if ok and woken.woken:
                command = woken.remainder or "现在几点了"
                turn = agent.respond(command)
                print(f"      指令={command!r} → 回答：{turn.text}")
                for trace in turn.trace:
                    print(f"      工具轨迹：{trace.summary()}")

    asr.close()
    print(f"\n[3/3] 完成：{len(cases) - failed}/{len(cases)} 通过")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
