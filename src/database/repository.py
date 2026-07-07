import json
import logging
from typing import Optional
from .models import get_connection

logger = logging.getLogger(__name__)


class Repository:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _conn(self):
        return get_connection(self.db_path)

    # ── News ───────────────────────────────────────────────────────────────

    def upsert_news(self, articles: list[dict]) -> int:
        sql = """
            INSERT OR IGNORE INTO news
                (date, category, source, group_name, title, url, summary, published_at)
            VALUES
                (:date, :category, :source, :group_name, :title, :url, :summary, :published_at)
        """
        inserted = 0
        with self._conn() as conn:
            for a in articles:
                cur = conn.execute(sql, {
                    "date": a.get("date"),
                    "category": a.get("category", "general"),
                    "source": a.get("source", ""),
                    "group_name": a.get("group_name"),
                    "title": a.get("title", ""),
                    "url": a.get("url", ""),
                    "summary": a.get("summary", ""),
                    "published_at": a.get("published_at", ""),
                })
                inserted += cur.rowcount
        logger.info(f"ニュース {inserted}/{len(articles)} 件を新規挿入")
        return inserted

    def get_news_for_date(self, target_date: str, limit: int = 60) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM news
                   WHERE date=?
                   ORDER BY
                     CASE category
                       WHEN 'disbandment'   THEN 1
                       WHEN 'member_change' THEN 2
                       WHEN 'live_report'   THEN 3
                       ELSE 4
                     END,
                     published_at DESC
                   LIMIT ?""",
                (target_date, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_recently_used_urls(self, before_date: str, lookback_days: int) -> set:
        """直近lookback_days日分ですでにエピソード化(used_in_episode=1)済みのURL集合を返す。

        これらは翌日以降の収集結果からあらかじめ除外し、同じネタが連日
        再登場するのを防ぐために使う。
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT DISTINCT url FROM news
                   WHERE used_in_episode=1
                     AND url != ''
                     AND date >= date(?, ?)
                     AND date < ?""",
                (before_date, f"-{lookback_days} days", before_date)
            ).fetchall()
        return {r["url"] for r in rows}

    def mark_news_used(self, target_date: str) -> None:
        """指定日にAI分析へ渡したニュースをすべて使用済みとしてマークする。"""
        with self._conn() as conn:
            conn.execute(
                "UPDATE news SET used_in_episode=1 WHERE date=?", (target_date,)
            )

    def get_news_by_category(self, target_date: str, category: str,
                             limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM news
                   WHERE date=? AND category=?
                   ORDER BY published_at DESC
                   LIMIT ?""",
                (target_date, category, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_news(self, days: int = 2, limit: int = 60) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM news
                   WHERE date >= date('now', ?, 'localtime')
                   ORDER BY published_at DESC
                   LIMIT ?""",
                (f"-{days} days", limit)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Analysis ───────────────────────────────────────────────────────────

    def save_analysis(self, target_date: str, analysis: dict) -> None:
        sql = """
            INSERT INTO analysis
                (date, scene_summary, spotlight_topics, member_changes,
                 upcoming_events, trending_themes, keywords, raw_prompt, model_used)
            VALUES
                (:date, :scene_summary, :spotlight_topics, :member_changes,
                 :upcoming_events, :trending_themes, :keywords, :raw_prompt, :model_used)
            ON CONFLICT(date) DO UPDATE SET
                scene_summary=excluded.scene_summary,
                spotlight_topics=excluded.spotlight_topics,
                member_changes=excluded.member_changes,
                upcoming_events=excluded.upcoming_events,
                trending_themes=excluded.trending_themes,
                keywords=excluded.keywords,
                raw_prompt=excluded.raw_prompt,
                model_used=excluded.model_used
        """
        with self._conn() as conn:
            conn.execute(sql, {
                "date": target_date,
                "scene_summary": analysis.get("scene_summary", ""),
                "spotlight_topics": json.dumps(
                    analysis.get("spotlight_topics", []), ensure_ascii=False),
                "member_changes": json.dumps(
                    analysis.get("member_changes", []), ensure_ascii=False),
                "upcoming_events": analysis.get("upcoming_events", ""),
                "trending_themes": json.dumps(
                    analysis.get("trending_themes", []), ensure_ascii=False),
                "keywords": json.dumps(
                    analysis.get("keywords", []), ensure_ascii=False),
                "raw_prompt": analysis.get("raw_prompt", ""),
                "model_used": analysis.get("model_used", ""),
            })
        logger.info(f"分析結果を保存: {target_date}")

    def get_analysis(self, target_date: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM analysis WHERE date=?", (target_date,)
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        for field in ("spotlight_topics", "member_changes", "trending_themes", "keywords"):
            if result.get(field):
                try:
                    result[field] = json.loads(result[field])
                except json.JSONDecodeError:
                    result[field] = []
        return result

    # ── Episodes ───────────────────────────────────────────────────────────

    def save_episode(self, episode: dict) -> None:
        sql = """
            INSERT INTO episodes
                (date, title, script_path, audio_path, duration_seconds, file_size_bytes)
            VALUES
                (:date, :title, :script_path, :audio_path, :duration_seconds, :file_size_bytes)
            ON CONFLICT(date) DO UPDATE SET
                title=excluded.title,
                script_path=excluded.script_path,
                audio_path=excluded.audio_path,
                duration_seconds=excluded.duration_seconds,
                file_size_bytes=excluded.file_size_bytes
        """
        with self._conn() as conn:
            conn.execute(sql, {
                "date": episode["date"],
                "title": episode["title"],
                "script_path": episode.get("script_path", ""),
                "audio_path": episode.get("audio_path", ""),
                "duration_seconds": episode.get("duration_seconds"),
                "file_size_bytes": episode.get("file_size_bytes"),
            })
        logger.info(f"エピソード保存: {episode['date']}")

    def get_recent_episodes(self, limit: int = 7) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM episodes ORDER BY date DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_episode_published(self, target_date: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE episodes SET rss_published=1 WHERE date=?", (target_date,)
            )
