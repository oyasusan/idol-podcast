import logging
import time
from datetime import datetime, timedelta
from urllib.parse import quote

import feedparser
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# IDOL REPORT.com カテゴリ別RSSフィード
IDOL_REPORT_FEEDS = {
    "member_change": "https://idol-report.com/category/member/feed/",
    "disbandment":   "https://idol-report.com/category/%E8%A7%A3%E6%95%A3/feed/",
    "live_report":   "https://idol-report.com/category/%E3%83%A9%E3%82%A4%E3%83%96%E3%83%AC%E3%83%9D%E3%83%BC%E3%83%88/feed/",
}

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"
PR_TIMES_SEARCH = "https://prtimes.jp/topics/keywords/%E5%9C%B0%E4%B8%8B%E3%82%A2%E3%82%A4%E3%83%89%E3%83%AB"

GOOGLE_NEWS_QUERIES = [
    "地下アイドル OR ライブアイドル 解散",
    "地下アイドル OR ライブアイドル 脱退 卒業",
    "地下アイドル OR ライブアイドル ライブ ワンマン",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; IdolPodcastBot/1.0)"
    )
}


class IdolNewsCollector:
    def __init__(self, settings: dict):
        self.max_age_hours = settings.get("news", {}).get("article_age_limit_hours", 48)
        self.max_per_feed = settings.get("news", {}).get("max_articles_per_feed", 10)

    def _is_recent(self, published_at: str) -> bool:
        if not published_at:
            return True
        try:
            pub = datetime.strptime(published_at[:19], "%Y-%m-%dT%H:%M:%S")
            return datetime.now() - pub < timedelta(hours=self.max_age_hours)
        except ValueError:
            return True

    def _parse_entry(self, entry, category: str, source: str,
                     target_date: str) -> dict | None:
        title = getattr(entry, "title", "") or ""
        url = getattr(entry, "link", "") or ""
        summary_raw = getattr(entry, "summary", "") or ""

        published_at = ""
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            dt = datetime(*entry.published_parsed[:6])
            published_at = dt.isoformat()

        if not self._is_recent(published_at):
            return None

        summary = BeautifulSoup(summary_raw, "html.parser").get_text()[:500]

        # タイトルからグループ名を推定（最初の空白・記号前）
        group_name = self._extract_group_name(title)

        return {
            "date": target_date,
            "category": category,
            "source": source,
            "group_name": group_name,
            "title": title[:300],
            "url": url[:500],
            "summary": summary,
            "published_at": published_at,
        }

    def _extract_group_name(self, title: str) -> str | None:
        """タイトル先頭のグループ名を抽出する（スペース・記号で区切られた最初の部分）。"""
        for sep in [" ", "　", "　", "【"]:
            if sep in title:
                candidate = title.split(sep)[0].strip()
                if 1 < len(candidate) < 40:
                    return candidate
        return None

    def fetch_idol_report(self, target_date: str) -> list[dict]:
        """IDOL REPORT.com のカテゴリ別RSSを取得。"""
        articles = []
        for category, feed_url in IDOL_REPORT_FEEDS.items():
            try:
                feed = feedparser.parse(feed_url)
                count = 0
                for entry in feed.entries:
                    article = self._parse_entry(
                        entry, category, "IDOL REPORT.com", target_date
                    )
                    if article:
                        articles.append(article)
                        count += 1
                        if count >= self.max_per_feed:
                            break
                logger.info(f"IDOL REPORT [{category}]: {count}件取得")
            except Exception as e:
                logger.warning(f"IDOL REPORT取得失敗 [{category}]: {e}")
            time.sleep(1)
        return articles

    def fetch_google_news(self, target_date: str) -> list[dict]:
        """Google News RSSで地下アイドル/ライブアイドル関連ニュースを取得。"""
        articles = []
        seen_urls: set = set()
        for query in GOOGLE_NEWS_QUERIES:
            url = GOOGLE_NEWS_RSS.format(query=quote(query))
            try:
                feed = feedparser.parse(url)
                count = 0
                for entry in feed.entries:
                    article = self._parse_entry(
                        entry, "general", "Google News", target_date
                    )
                    if article and article["url"] not in seen_urls:
                        articles.append(article)
                        seen_urls.add(article["url"])
                        count += 1
                        if count >= self.max_per_feed:
                            break
                logger.info(f"Google News [{query[:20]}…]: {count}件取得")
            except Exception as e:
                logger.warning(f"Google News取得失敗 ({query}): {e}")
            time.sleep(1.5)
        return articles

    def fetch_prtimes(self, target_date: str) -> list[dict]:
        """PR TIMESの「地下アイドル」トピックページから記事を取得。"""
        articles = []
        try:
            resp = requests.get(PR_TIMES_SEARCH, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, "lxml")
            for article_el in soup.select("article.list-article")[:self.max_per_feed]:
                a_tag = article_el.find("a", href=True)
                title_el = article_el.find(["h2", "h3"])
                if not a_tag or not title_el:
                    continue
                title = title_el.get_text(strip=True)
                url = "https://prtimes.jp" + a_tag["href"] if a_tag["href"].startswith("/") else a_tag["href"]
                articles.append({
                    "date": target_date,
                    "category": "general",
                    "source": "PR TIMES",
                    "group_name": self._extract_group_name(title),
                    "title": title[:300],
                    "url": url[:500],
                    "summary": "",
                    "published_at": "",
                })
            logger.info(f"PR TIMES: {len(articles)}件取得")
        except Exception as e:
            logger.warning(f"PR TIMES取得失敗: {e}")
        return articles

    def fetch_all(self, target_date: str) -> list[dict]:
        """全ソースからニュースを収集して重複排除して返す。"""
        all_articles: list[dict] = []

        logger.info("IDOL REPORT.com からニュース収集中...")
        all_articles.extend(self.fetch_idol_report(target_date))

        logger.info("Google News からニュース収集中...")
        all_articles.extend(self.fetch_google_news(target_date))

        logger.info("PR TIMES からニュース収集中...")
        all_articles.extend(self.fetch_prtimes(target_date))

        seen_urls: set = set()
        unique: list[dict] = []
        for a in all_articles:
            url = a.get("url", "")
            if url not in seen_urls:
                unique.append(a)
                if url:
                    seen_urls.add(url)

        logger.info(f"ニュース収集完了: 合計{len(unique)}件")
        return unique
