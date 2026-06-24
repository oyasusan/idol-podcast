import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _run_ffmpeg(cmd: list[str]) -> subprocess.CompletedProcess:
    logger.debug(f"FFmpeg実行: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"FFmpegエラー: {result.stderr[-500:]}")
        raise RuntimeError(f"FFmpeg failed (rc={result.returncode})")
    return result


class FFmpegProcessor:
    def __init__(self, settings: dict, assets_dir: str = "assets"):
        audio_cfg = settings.get("audio", {})
        self.bitrate = audio_cfg.get("bitrate", "128k")
        self.sample_rate = audio_cfg.get("sample_rate", 44100)
        self.channels = audio_cfg.get("channels", 2)
        self.normalize_loudness = audio_cfg.get("normalize_loudness", -16.0)
        self.bgm_volume_db = audio_cfg.get("bgm_volume_db", -20)
        self.assets_dir = Path(assets_dir)

    def process(self, voice_mp3: str, output_mp3: str,
                episode_date: str, episode_title: str,
                episode_number: Optional[int] = None) -> bool:
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)

                normalized = tmp / "normalized.mp3"
                self._normalize_loudness(voice_mp3, str(normalized))

                with_opening_ending = tmp / "with_jingles.mp3"
                self._add_opening_ending(str(normalized), str(with_opening_ending), tmpdir)

                with_bgm = tmp / "with_bgm.mp3"
                bgm_added = self._mix_bgm(str(with_opening_ending), str(with_bgm))
                source_for_final = str(with_bgm) if bgm_added else str(with_opening_ending)

                final_normalized = tmp / "final_normalized.mp3"
                self._normalize_loudness(source_for_final, str(final_normalized))

                self._export_mp3(str(final_normalized), output_mp3,
                                 episode_date, episode_title, episode_number)

            logger.info(f"音声処理完了: {output_mp3}")
            return True

        except Exception as e:
            logger.error(f"音声処理失敗: {e}")
            try:
                self._export_mp3(voice_mp3, output_mp3, episode_date, episode_title, episode_number)
                logger.warning("フォールバック: ボイスのみで出力")
                return True
            except Exception as e2:
                logger.error(f"フォールバックも失敗: {e2}")
                return False

    def _normalize_loudness(self, input_path: str, output_path: str) -> None:
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-af", f"loudnorm=I={self.normalize_loudness}:TP=-1.5:LRA=11",
            "-ar", str(self.sample_rate),
            "-ac", str(self.channels),
            "-c:a", "libmp3lame",
            "-b:a", self.bitrate,
            output_path,
        ]
        _run_ffmpeg(cmd)

    def _add_opening_ending(self, voice_path: str, output_path: str, tmpdir: str) -> None:
        opening_path = self.assets_dir / "opening.mp3"
        ending_path = self.assets_dir / "ending.mp3"
        has_opening = opening_path.exists() and opening_path.stat().st_size > 0
        has_ending = ending_path.exists() and ending_path.stat().st_size > 0

        if not has_opening and not has_ending:
            import shutil
            shutil.copy2(voice_path, output_path)
            return

        concat_list = Path(tmpdir) / "jingle_concat.txt"
        files_to_concat = []
        if has_opening:
            files_to_concat.append(str(opening_path))
        files_to_concat.append(voice_path)
        if has_ending:
            files_to_concat.append(str(ending_path))

        with open(concat_list, "w") as f:
            for fp in files_to_concat:
                f.write(f"file '{fp}'\n")

        _run_ffmpeg([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            output_path,
        ])

    def _mix_bgm(self, voice_path: str, output_path: str) -> bool:
        bgm_path = self.assets_dir / "bgm.mp3"
        if not bgm_path.exists() or bgm_path.stat().st_size == 0:
            return False
        try:
            probe_cmd = [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                voice_path,
            ]
            result = subprocess.run(probe_cmd, capture_output=True, text=True)
            duration = float(result.stdout.strip())
            _run_ffmpeg([
                "ffmpeg", "-y",
                "-i", voice_path,
                "-stream_loop", "-1", "-i", str(bgm_path),
                "-filter_complex",
                (
                    f"[1:a]volume={self.bgm_volume_db}dB,"
                    f"atrim=0:{duration}[bgm];"
                    f"[0:a][bgm]amix=inputs=2:duration=first[out]"
                ),
                "-map", "[out]",
                "-c:a", "libmp3lame",
                "-b:a", self.bitrate,
                output_path,
            ])
            return True
        except Exception as e:
            logger.warning(f"BGMミックス失敗（スキップ）: {e}")
            return False

    def _export_mp3(self, input_path: str, output_path: str,
                    episode_date: str, episode_title: str,
                    episode_number: Optional[int]) -> None:
        year = episode_date[:4]
        track = str(episode_number) if episode_number else ""
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-c:a", "libmp3lame",
            "-b:a", self.bitrate,
            "-ar", str(self.sample_rate),
            "-ac", str(self.channels),
            "-id3v2_version", "3",
            "-metadata", f"title={episode_title}",
            "-metadata", "artist=ライブアイドルデイリー",
            "-metadata", "album=ライブアイドルデイリー",
            "-metadata", f"date={year}",
            "-metadata", "genre=Podcast",
            "-metadata", "comment=ライブアイドルシーンの最新情報をお届けするPodcastです。",
        ]
        if track:
            cmd += ["-metadata", f"track={track}"]
        cmd.append(output_path)
        _run_ffmpeg(cmd)

    def get_duration(self, mp3_path: str) -> int:
        try:
            cmd = [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                mp3_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            return int(float(result.stdout.strip()))
        except Exception:
            return 0
