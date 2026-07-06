import json
import logging
import re
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
    "地下アイドル OR ライブアイドル 新曲 MV リリース",
    "地下アイドル OR ライブアイドル 対バン フェス 出演",
    "地下アイドル OR ライブアイドル 結成 デビュー 新加入",
    "地下アイドル OR ライブアイドル イベント 特典会",
]

GOOGLE_NEWS_SNS_QUERIES = [
    "地下アイドル 話題",
    "ライブアイドル バズ 注目",
    "地下アイドル SNS",
    "ライブアイドル トレンド 人気",
    "地下アイドル 炎上 OR 話題沸騰",
]

# Yahoo!リアルタイム検索（X/旧Twitterの投稿をログイン不要で検索できる公開ページ）
YAHOO_REALTIME_SEARCH_URL = "https://search.yahoo.co.jp/realtime/search?p={query}&ei=UTF-8"

YAHOO_REALTIME_QUERIES = [
    "地下アイドル",
    "ライブアイドル",
]

NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
YAHOO_HIGHLIGHT_TAG_RE = re.compile(r"\tSTART\t|\tEND\t")

# liveidol.blog（全国のアイドルライブスケジュールまとめサイト）
LIVEIDOL_SCHEDULE_URL = "https://liveidol.blog/live/"
SCHEDULE_DATA_RE = re.compile(r"const scheduleData = (\[.*?\]);", re.S)

# 「重要度が高い」と判定するための基準
LIVEIDOL_MAJOR_VENUE_KEYWORDS = [
    "日本武道館", "東京ドーム", "さいたまスーパーアリーナ", "横浜アリーナ", "Zepp",
    "幕張メッセ", "有明アリーナ", "国立代々木競技場", "大阪城ホール", "日本ガイシホール",
    "ぴあアリーナ", "東京国際フォーラム", "東京ガーデンシアター", "パシフィコ横浜",
    "豊洲PIT", "マリンスタジアム", "中野サンプラザ", "なんばHatch", "LINE CUBE",
]
LIVEIDOL_MEMBER_CHANGE_KEYWORDS = ["卒業", "解散", "ラスト", "FINAL", "ファイナル", "LAST", "脱退"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; IdolPodcastBot/1.0)"
    )
}


class IdolNewsCollector:
    def __init__(self, settings: dict):
        self.max_age_hours = settings.get("news", {}).get("article_age_limit_hours", 48)
        self.max_per_feed = settings.get("news", {}).get("max_articles_per_feed", 10)

    def _is_in_window(self, published_at: str, target_date: str) -> bool:
        """target_dateの翌日0時から max_age_hours 時間遡った範囲内かを判定する。"""
        if not published_at:
            return True
        try:
            pub = datetime.strptime(published_at[:19], "%Y-%m-%dT%H:%M:%S")
            # target_date の翌日0時を上限とし、そこから max_age_hours 時間前を下限とする
            window_end = datetime.fromisoformat(target_date) + timedelta(days=1)
            window_start = window_end - timedelta(hours=self.max_age_hours)
            return window_start <= pub <= window_end
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

        if not self._is_in_window(published_at, target_date):
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

    def fetch_sns_trending(self, target_date: str) -> list[dict]:
        """Google News RSSでSNSバズ・話題関連ニュースを取得。"""
        articles = []
        seen_urls: set = set()
        for query in GOOGLE_NEWS_SNS_QUERIES:
            url = GOOGLE_NEWS_RSS.format(query=quote(query))
            try:
                feed = feedparser.parse(url)
                count = 0
                for entry in feed.entries:
                    article = self._parse_entry(
                        entry, "sns_trending", "Google News", target_date
                    )
                    if article and article["url"] not in seen_urls:
                        articles.append(article)
                        seen_urls.add(article["url"])
                        count += 1
                        if count >= self.max_per_feed:
                            break
                logger.info(f"Google News SNSトレンド [{query[:20]}…]: {count}件取得")
            except Exception as e:
                logger.warning(f"Google News SNSトレンド取得失敗 ({query}): {e}")
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

    def fetch_x_buzz(self, target_date: str) -> list[dict]:
        """Yahoo!リアルタイム検索経由でX（旧Twitter）上の反響が大きい投稿を取得する。

        タイムライン（新着順）は反響が薄いため使わず、Yahooが選ぶ「ベストポスト」
        （検索クエリに対して最もエンゲージメントが高い投稿）のみを採用する。
        """
        articles = []
        seen_ids: set = set()
        for query in YAHOO_REALTIME_QUERIES:
            try:
                url = YAHOO_REALTIME_SEARCH_URL.format(query=quote(query))
                resp = requests.get(url, headers=HEADERS, timeout=15)
                resp.raise_for_status()
                m = NEXT_DATA_RE.search(resp.text)
                if not m:
                    logger.warning(f"Yahoo!リアルタイム検索 [{query}]: データ抽出失敗")
                    continue
                page_data = (
                    json.loads(m.group(1))
                    .get("props", {})
                    .get("pageProps", {})
                    .get("pageData", {})
                )

                best_tweet = page_data.get("bestTweet")
                tw_id = (best_tweet or {}).get("id")
                if not tw_id or tw_id in seen_ids:
                    logger.info(f"Yahoo!リアルタイム検索 [{query}]: 0件取得")
                    continue

                created_at = best_tweet.get("createdAt")
                published_at = (
                    datetime.fromtimestamp(created_at).isoformat() if created_at else ""
                )
                # bestTweetは「今まさに反響が大きい投稿」というリアルタイム値であり、
                # RSS記事のような過去ログではないため日付ウィンドウでは絞り込まない。

                text = YAHOO_HIGHLIGHT_TAG_RE.sub("", best_tweet.get("displayText", "")).strip()
                if not text:
                    continue

                seen_ids.add(tw_id)
                likes = best_tweet.get("likesCount", 0)
                rts = best_tweet.get("rtCount", 0)
                qts = best_tweet.get("qtCount", 0)
                articles.append({
                    "date": target_date,
                    "category": "sns_trending",
                    "source": "Yahoo!リアルタイム検索",
                    "group_name": query,
                    "title": text[:300],
                    "url": best_tweet.get("url", "")[:500],
                    "summary": f"いいね{likes}件・RT{rts}件・引用{qts}件（X/旧Twitterでの反響）",
                    "published_at": published_at,
                })
                logger.info(f"Yahoo!リアルタイム検索 [{query}]: 1件取得（いいね{likes}件）")
            except Exception as e:
                logger.warning(f"Yahoo!リアルタイム検索取得失敗 ({query}): {e}")
            time.sleep(1.5)
        return articles

    def fetch_upcoming_live_events(self, target_date: str) -> list[dict]:
        """liveidol.blogのライブスケジュールから、重要イベントを取得する。

        「重要」の基準は、大型会場（Zepp・アリーナ・武道館など）での開催、または
        卒業・解散・ラストライブなどメンバー動向に関わるものの二種類。
        通常のライブハウス対バン（1日あたり数十件規模）はここでは拾わない。
        当日開催分を優先し、当日に該当がなければ今後1週間分で補う。
        """
        articles = []
        try:
            resp = requests.get(LIVEIDOL_SCHEDULE_URL, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            m = SCHEDULE_DATA_RE.search(resp.text)
            if not m:
                logger.warning("liveidol.blog: スケジュールデータ抽出失敗")
                return articles
            schedule = json.loads(m.group(1))

            start = datetime.fromisoformat(target_date).date()
            week_end = start + timedelta(days=6)

            def collect(date_from, date_to) -> list:
                seen_keys: set = set()
                picks = []
                for ev in schedule:
                    try:
                        ev_date = datetime.strptime(ev.get("event_date", ""), "%Y-%m-%d").date()
                    except ValueError:
                        continue
                    if not (date_from <= ev_date <= date_to):
                        continue

                    name = ev.get("event_name", "")
                    venue = ev.get("venue_name", "")
                    is_major_venue = any(v in venue for v in LIVEIDOL_MAJOR_VENUE_KEYWORDS)
                    is_member_change = any(k in name for k in LIVEIDOL_MEMBER_CHANGE_KEYWORDS)
                    if not (is_major_venue or is_member_change):
                        continue

                    key = (ev.get("event_date"), name, venue)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    picks.append((ev_date, ev))

                picks.sort(key=lambda x: x[0])
                return picks

            # 当日開催分を優先。当日に該当がなければ今後1週間分で補う。
            picks = collect(start, start)
            if not picks:
                picks = collect(start, week_end)

            for ev_date, ev in picks[:20]:
                performers = ev.get("performers", "")
                articles.append({
                    "date": target_date,
                    "category": "upcoming_live_event",
                    "source": "liveidol.blog",
                    "group_name": None,
                    "title": f"{ev.get('date_display', '')} {ev.get('event_name', '')}（{ev.get('venue_name', '')}）",
                    "url": ev.get("event_url", "")[:500],
                    "summary": f"出演: {performers[:150]}" if performers else "",
                    "published_at": "",
                })
            logger.info(f"liveidol.blog: 重要ライブ {len(articles)}件取得")
        except Exception as e:
            logger.warning(f"liveidol.blog取得失敗: {e}")
        return articles

    def fetch_all(self, target_date: str) -> list[dict]:
        """全ソースからニュースを収集して重複排除して返す。"""
        all_articles: list[dict] = []

        logger.info("IDOL REPORT.com からニュース収集中...")
        all_articles.extend(self.fetch_idol_report(target_date))

        logger.info("Google News からニュース収集中...")
        all_articles.extend(self.fetch_google_news(target_date))

        logger.info("Google News SNSトレンド収集中...")
        all_articles.extend(self.fetch_sns_trending(target_date))

        logger.info("Yahoo!リアルタイム検索からXバズ情報収集中...")
        all_articles.extend(self.fetch_x_buzz(target_date))

        logger.info("liveidol.blog から今後のライブ情報収集中...")
        all_articles.extend(self.fetch_upcoming_live_events(target_date))

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
