"""
TTSエンジン。
ミク（女性: ja-JP-NanamiNeural）とリョウ（男性: ja-JP-KeitaNeural）の
2音声に対応したedge-ttsベースの音声合成。
"""
import asyncio
import logging
import re
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

VOICE_MAP = {
    "ミク": "ja-JP-NanamiNeural",
    "リョウ": "ja-JP-KeitaNeural",
}
DEFAULT_VOICE = "ja-JP-NanamiNeural"

SPEAKER_RE = re.compile(r"^(ミク|リョウ)：(.*)$")


def _parse_dialogue(script: str) -> list[tuple[str, str]]:
    segments: list[tuple[str, str]] = []
    current_speaker = "ミク"
    current_lines: list[str] = []

    def flush():
        text = " ".join(t for t in current_lines if t)
        if text:
            segments.append((current_speaker, text))
        current_lines.clear()

    for raw in script.split("\n"):
        stripped = raw.strip()
        m = SPEAKER_RE.match(stripped)
        if m:
            flush()
            current_speaker = m.group(1)
            remainder = m.group(2).strip()
            if remainder:
                current_lines.append(remainder)
        elif stripped:
            current_lines.append(stripped)
        else:
            flush()

    flush()
    return segments


def _clean_segment(text: str) -> str:
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


async def _edge_tts_generate(text: str, output_path: str, voice: str,
                              rate: str, volume: str) -> bool:
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
        await communicate.save(output_path)
        return True
    except Exception as e:
        logger.error(f"edge-tts失敗 [{voice}]: {e}")
        return False


class TTSEngine:
    def __init__(self, settings: dict):
        import os
        tts_cfg = settings.get("tts", {})
        self.rate = os.getenv("TTS_RATE", tts_cfg.get("rate", "+10%"))
        self.volume = os.getenv("TTS_VOLUME", tts_cfg.get("volume", "+0%"))

    def generate(self, script: str, output_path: str) -> bool:
        segments = _parse_dialogue(script)
        if not segments:
            logger.error("解析できるセグメントがありません")
            return False

        logger.info(f"TTS生成開始: {len(segments)}セグメント -> {output_path}")
        temp_files: list[str] = []
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                for i, (speaker, text) in enumerate(segments):
                    cleaned = _clean_segment(text)
                    if not cleaned:
                        continue
                    voice = VOICE_MAP.get(speaker, DEFAULT_VOICE)
                    seg_path = str(Path(tmpdir) / f"seg_{i:04d}.mp3")
                    ok = asyncio.run(
                        _edge_tts_generate(cleaned, seg_path, voice, self.rate, self.volume)
                    )
                    if ok and Path(seg_path).exists() and Path(seg_path).stat().st_size > 0:
                        temp_files.append(seg_path)
                    else:
                        logger.warning(f"セグメント{i}({speaker})の生成失敗")

                if not temp_files:
                    logger.error("生成成功セグメントが0件")
                    return False

                if len(temp_files) == 1:
                    import shutil
                    shutil.copy2(temp_files[0], output_path)
                else:
                    self._concatenate(temp_files, output_path, tmpdir)

        except Exception as e:
            logger.error(f"TTS生成エラー: {e}")
            return False

        if Path(output_path).exists() and Path(output_path).stat().st_size > 0:
            logger.info(f"TTS生成完了: {output_path}")
            return True
        logger.error(f"出力ファイルが空または存在しない: {output_path}")
        return False

    def _concatenate(self, mp3_files: list[str], output_path: str, tmpdir: str) -> None:
        list_file = Path(tmpdir) / "concat.txt"
        with open(list_file, "w", encoding="utf-8") as f:
            for fp in mp3_files:
                f.write(f"file '{fp}'\n")
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {result.stderr[-300:]}")
        logger.info(f"{len(mp3_files)}セグメントを結合")
