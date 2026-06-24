#!/usr/bin/env python3
"""ライブアイドルデイリー Podcast自動生成システム - メインオーケストレーター"""

import argparse
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _load_settings() -> dict:
    with open(BASE_DIR / "config" / "settings.yml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _get_paths(settings: dict, target_date: str) -> dict:
    # data（DB）はSDカードのI/O制限を避けホームディレクトリに置く
    home_data = Path.home() / ".idol-podcast"
    data_dir     = home_data / settings.get("system", {}).get("data_dir", "data")
    output_dir   = BASE_DIR / settings.get("system", {}).get("output_dir", "output")
    docs_dir     = BASE_DIR / settings.get("system", {}).get("docs_dir", "docs")
    assets_dir   = BASE_DIR / settings.get("system", {}).get("assets_dir", "assets")
    episodes_dir = docs_dir / "episodes"

    for d in [data_dir, output_dir, docs_dir, assets_dir, episodes_dir]:
        d.mkdir(parents=True, exist_ok=True)

    return {
        "db":           str(data_dir / "database.sqlite"),
        "script":       str(output_dir / f"script_{target_date}.txt"),
        "voice_mp3":    str(output_dir / f"voice_{target_date}.mp3"),
        "podcast_mp3":  str(episodes_dir / f"podcast_{target_date}.mp3"),
        "analysis_json": str(output_dir / "analysis.json"),
        "rss_xml":      str(docs_dir / "feed.xml"),
        "docs_dir":     str(docs_dir),
        "episodes_dir": str(episodes_dir),
        "assets_dir":   str(assets_dir),
    }


def run(target_date: str, settings: dict, dry_run: bool = False) -> bool:
    logger = logging.getLogger("main")
    logger.info(f"=== ライブアイドルデイリー Podcast生成開始: {target_date} ===")

    paths = _get_paths(settings, target_date)

    # ── DB初期化 ─────────────────────────────────────────────────────────
    from .database.models import initialize_database
    from .database.repository import Repository
    initialize_database(paths["db"])
    repo = Repository(paths["db"])

    # ── Step 1: ニュース収集 ───────────────────────────────────────────
    logger.info("Step 1: ニュース収集")
    from .collectors.idol_news_collector import IdolNewsCollector
    collector = IdolNewsCollector(settings)
    all_news = collector.fetch_all(target_date)
    repo.upsert_news(all_news)

    # ── Step 2: AI分析 ─────────────────────────────────────────────────
    logger.info("Step 2: AI分析")
    from .analyzers.ai_analyzer import AIAnalyzer
    analyzer = AIAnalyzer(settings)

    fresh_news = repo.get_recent_news(
        days=settings.get("news", {}).get("article_age_limit_hours", 48) // 24 + 1,
        limit=60,
    )
    logger.info(f"分析対象ニュース: {len(fresh_news)}件")

    if not dry_run:
        analysis = analyzer.analyze(target_date, fresh_news)
        repo.save_analysis(target_date, analysis)
    else:
        logger.info("Dry run: AI分析をスキップ")
        analysis = {
            "scene_summary": "Dry run - AI分析スキップ",
            "spotlight_topics": [],
            "member_changes": [],
            "upcoming_events": "Dry run",
            "trending_themes": [],
            "keywords": [],
            "model_used": "dry_run",
        }

    # ── Step 3: 台本生成 ───────────────────────────────────────────────
    logger.info("Step 3: 台本生成")
    from .generators.script_generator import ScriptGenerator
    script_gen = ScriptGenerator(settings)
    script = script_gen.generate(target_date, fresh_news, analysis)
    with open(paths["script"], "w", encoding="utf-8") as f:
        f.write(script)
    logger.info(f"台本保存: {paths['script']} ({len(script)}文字)")

    # analysis.json 保存（デバッグ用）
    with open(paths["analysis_json"], "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)

    if dry_run:
        logger.info("Dry run: 音声生成をスキップ")
        logger.info(f"\n{'='*60}\n台本プレビュー:\n{script[:1000]}...\n{'='*60}")
        return True

    # ── Step 4: TTS音声生成 ────────────────────────────────────────────
    logger.info("Step 4: TTS音声生成")
    from .tts.tts_engine import TTSEngine
    tts = TTSEngine(settings)
    success = tts.generate(script, paths["voice_mp3"])
    if not success:
        logger.error("TTS生成失敗")
        return False

    # ── Step 5: FFmpeg音声処理 ─────────────────────────────────────────
    logger.info("Step 5: FFmpeg音声処理")
    from .audio.ffmpeg_processor import FFmpegProcessor
    ffmpeg = FFmpegProcessor(settings, assets_dir=paths["assets_dir"])
    episode_title = f"{target_date} ライブアイドルデイリー"
    success = ffmpeg.process(
        voice_mp3=paths["voice_mp3"],
        output_mp3=paths["podcast_mp3"],
        episode_date=target_date,
        episode_title=episode_title,
    )
    if not success:
        logger.error("FFmpeg処理失敗")
        return False

    # ── Step 6: エピソード情報DB保存 ──────────────────────────────────
    duration = ffmpeg.get_duration(paths["podcast_mp3"])
    file_size = (
        Path(paths["podcast_mp3"]).stat().st_size
        if Path(paths["podcast_mp3"]).exists() else 0
    )
    repo.save_episode({
        "date": target_date,
        "title": episode_title,
        "script_path": paths["script"],
        "audio_path": paths["podcast_mp3"],
        "duration_seconds": duration,
        "file_size_bytes": file_size,
    })
    logger.info(f"エピソード情報保存: {duration}秒 / {file_size:,}bytes")

    # ── Step 7: RSS更新 ───────────────────────────────────────────────
    logger.info("Step 7: RSS更新")
    from .generators.rss_generator import RSSGenerator
    rss_gen = RSSGenerator(settings)
    recent_episodes = repo.get_recent_episodes(
        limit=settings.get("podcast", {}).get("max_episodes_in_rss", 7)
    )
    rss_xml = rss_gen.generate(recent_episodes)
    with open(paths["rss_xml"], "w", encoding="utf-8") as f:
        f.write(rss_xml)
    logger.info(f"RSS更新: {paths['rss_xml']}")
    repo.mark_episode_published(target_date)

    # ── Step 8: 後処理 ────────────────────────────────────────────────
    logger.info("Step 8: 後処理")
    _cleanup_old_mp3(
        paths["episodes_dir"],
        keep_days=settings.get("podcast", {}).get("max_mp3_storage_days", 7)
    )
    if settings.get("cleanup", {}).get("delete_raw_voice", True):
        raw_voice = Path(paths["voice_mp3"])
        if raw_voice.exists():
            raw_voice.unlink()
            logger.info(f"中間ファイル削除: {paths['voice_mp3']}")

    logger.info(f"=== 完了: {target_date} ===")
    return True


def _cleanup_old_mp3(episodes_dir: str, keep_days: int = 7) -> None:
    logger = logging.getLogger("cleanup")
    cutoff = date.today() - timedelta(days=keep_days)
    deleted = 0
    for mp3_file in Path(episodes_dir).glob("podcast_*.mp3"):
        date_str = mp3_file.stem.replace("podcast_", "")
        try:
            if date.fromisoformat(date_str) < cutoff:
                mp3_file.unlink()
                deleted += 1
                logger.info(f"古いMP3削除: {mp3_file.name}")
        except ValueError:
            continue
    logger.info(f"{deleted}件の古いMP3を削除" if deleted else "削除対象のMP3なし")


def main():
    parser = argparse.ArgumentParser(description="ライブアイドルデイリー Podcast生成システム")
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="対象日付 (YYYY-MM-DD)。デフォルト: 今日",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="AI分析・音声生成をスキップ（テスト用）",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()
    _setup_logging(args.log_level)
    settings = _load_settings()
    success = run(args.date, settings, dry_run=args.dry_run)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
