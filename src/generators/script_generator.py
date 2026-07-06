"""
台本生成モジュール。
ミク（女性アナウンサー）とリョウ（男性ライター）の掛け合いで構成。
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

SOURCE_LABELS = {
    "IDOL REPORT.com": "アイドルレポートドットコム",
    "Google News":     "グーグルニュース",
    "PR TIMES":        "PRタイムス",
}

CATEGORY_LABELS = {
    "live_report":   "ライブレポート",
    "member_change": "メンバー変動",
    "disbandment":   "解散・活動終了",
    "general":       "一般ニュース",
}

CHANGE_TYPE_LABELS = {
    "卒業":     "卒業",
    "脱退":     "脱退",
    "加入":     "新加入",
    "解雇":     "契約解除・解雇",
    "活動終了": "グループ活動終了",
}

M = "ミク"
R = "リョウ"


def line(speaker: str, text: str) -> str:
    return f"{speaker}：{text}"


def _date_jp(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    return f"{dt.year}年{dt.month}月{dt.day}日（{weekdays[dt.weekday()]}）"


def _source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


def _norm_text(s: str) -> str:
    return "".join((s or "").split())


class ScriptGenerator:
    def __init__(self, settings: dict):
        self.settings = settings

    def generate(self, target_date: str, news: list[dict], analysis: dict) -> str:
        date_jp = _date_jp(target_date)
        sections = [
            self._opening(date_jp),
            self._scene_summary(analysis),
            self._spotlight_topics(analysis),
            self._member_changes(analysis, news),
            self._sns_trending(analysis),
            self._general_roundup(analysis, news),
            self._upcoming_events(analysis),
            self._ending(date_jp),
        ]
        script = "\n".join(ln for sec in sections for ln in sec if ln)
        logger.info(f"台本生成完了: {len(script)}文字")
        return script

    # ── Opening ───────────────────────────────────────────────────────────

    def _opening(self, date_jp: str) -> list[str]:
        return [
            line(M, f"おはようございます。ライブアイドルデイリー、{date_jp}版をお届けします。"),
            line(R, "おはようございます。リョウです。よろしくお願いします。"),
            line(M, "リョウさん、昨日から今朝にかけて、どんな動きがありましたか？"),
            line(R, "はい。ライブ情報にメンバー変動、解散の話題まで、いろいろと動きがありました。"),
            line(M, "それでは早速、確認していきましょう。情報の引用元はIDOL REPORT.com、Google Newsなどです。"),
        ]

    # ── Scene Summary ─────────────────────────────────────────────────────

    def _scene_summary(self, analysis: dict) -> list[str]:
        summary = analysis.get("scene_summary", "")
        if not summary:
            return []
        return [
            line(M, "まず、シーン全体の動向をざっくり教えてもらえますか？"),
            line(R, summary),
            line(M, "なるほど、幅広い動きがあったんですね。"),
        ]

    # ── Spotlight Topics ──────────────────────────────────────────────────

    SPOTLIGHT_FOLLOWUPS = [
        "それはどういった内容だったんですか？",
        "もう少し詳しく教えてもらえますか？",
        "こちらはどういった状況ですか？",
        "詳細も気になります。",
    ]
    SPOTLIGHT_ACKS = [
        "なるほど、それは気になりますね。",
        "そうなんですね。",
        "興味深いですね。",
        "それは注目です。",
    ]

    def _spotlight_topics(self, analysis: dict) -> list[str]:
        topics = analysis.get("spotlight_topics", [])
        # member_changeは_member_changesセクションで扱うため重複を除外
        topics = [t for t in topics if t.get("category") != "member_change"]
        if not topics:
            return []

        result = [line(M, "続きまして、特に注目のトピックをいくつか聞かせてください。まず何がありましたか？")]

        for i, topic in enumerate(topics[:5]):
            group = topic.get("group", "")
            headline = topic.get("headline", "")
            detail = topic.get("detail", "")
            source = _source_label(topic.get("source", ""))
            cat_label = CATEGORY_LABELS.get(topic.get("category", ""), "")
            source_text = f"引用元は{source}です。" if source else ""

            if i == 0:
                result.append(line(R, f"まず「{group}」の話題です。{cat_label}の情報で、{headline}"))
            elif i == 1:
                result.append(line(M, "ほかにはいかがでしたか？"))
                result.append(line(R, f"続いては「{group}」です。{headline}"))
            else:
                result.append(line(M, "まだありますか？"))
                result.append(line(R, f"もう一つ、「{group}」の件です。{headline}"))

            if detail:
                result.append(line(M, self.SPOTLIGHT_FOLLOWUPS[i % len(self.SPOTLIGHT_FOLLOWUPS)]))
                result.append(line(R, f"{detail}{source_text}"))
                result.append(line(M, self.SPOTLIGHT_ACKS[i % len(self.SPOTLIGHT_ACKS)]))

        result.append(line(M, "ありがとうございます。気になる動きが続いていますね。"))
        return result

    # ── Member Changes ────────────────────────────────────────────────────

    def _member_changes(self, analysis: dict, news: list[dict]) -> list[str]:
        changes = analysis.get("member_changes", [])

        # フォールバック: AIが返せなかった場合にニュースから直接生成
        if not changes:
            changes = self._extract_changes_from_news(news)

        if not changes:
            return []

        result = [
            line(M, "続いて、メンバーの動きについて教えてください。"),
            line(R, "はい。こちらは主にIDOL REPORT.comからの情報です。"),
        ]

        for i, ch in enumerate(changes[:12]):
            group = ch.get("group", "")
            member = ch.get("member", "")
            change_type = CHANGE_TYPE_LABELS.get(ch.get("change_type", ""), ch.get("change_type", ""))
            scheduled = ch.get("scheduled_date", "")
            detail = ch.get("detail", "")

            member_text = f"の{member}さん" if member else ""
            date_text = f"（{scheduled}付）" if scheduled else ""
            info = f"「{group}」{member_text}が{change_type}{date_text}。{detail}"

            if i == 0:
                result.append(line(R, info))
            elif i == 3:
                result.append(line(M, "まだありますか？"))
                result.append(line(R, f"はい、続きまして。{info}"))
            else:
                result.append(line(R, f"また、{info}"))

        result.append(line(M, "いくつものグループで動きがあったんですね。各グループの公式SNSでも最新情報を確認してみてください。"))
        return result

    def _extract_changes_from_news(self, news: list[dict]) -> list[dict]:
        changes = []
        for n in news:
            if n.get("category") != "member_change":
                continue
            title = n.get("title", "")
            change_type = "卒業"
            for kw, ct in [("脱退", "脱退"), ("解雇", "解雇"), ("加入", "加入"),
                            ("活動終了", "活動終了"), ("契約解除", "解雇")]:
                if kw in title:
                    change_type = ct
                    break
            changes.append({
                "group": n.get("group_name") or "不明",
                "member": "",
                "change_type": change_type,
                "scheduled_date": "",
                "detail": title[:50],
            })
        return changes

    # ── SNS Buzz Topics ───────────────────────────────────────────────────

    def _sns_trending(self, analysis: dict) -> list[str]:
        buzz_topics = analysis.get("sns_buzz_topics", [])
        if not buzz_topics:
            return []

        result = [
            line(M, "SNSではどんな話題が盛り上がっていましたか？"),
            line(R, "この日、特に注目されていた話題をいくつかピックアップしました。"),
        ]

        for i, topic in enumerate(buzz_topics[:4]):
            topic_name = topic.get("topic", "")
            description = topic.get("description", "")
            reason = topic.get("reason", "")

            if i == 0:
                result.append(line(R, f"まず「{topic_name}」ですね。{description}"))
            elif i == 1:
                result.append(line(M, "ほかにはありますか？"))
                result.append(line(R, f"「{topic_name}」も話題になっていました。{description}"))
            else:
                result.append(line(M, "まだありますか？"))
                result.append(line(R, f"「{topic_name}」も挙げておきたいです。{description}"))

            if reason:
                result.append(line(M, f"{reason}ということで注目されているんですね。"))

        result.append(line(M, "アイドルシーンはSNSとも切り離せませんね。引き続きチェックしていきましょう。"))
        return result

    # ── General News Roundup ─────────────────────────────────────────────

    def _general_roundup(self, analysis: dict, news: list[dict]) -> list[str]:
        """spotlight/member_changes/SNS話題で未使用の一般ニュースを短くまとめて紹介する。"""
        used_groups = {t.get("group") for t in analysis.get("spotlight_topics", []) if t.get("group")}
        used_groups |= {c.get("group") for c in analysis.get("member_changes", []) if c.get("group")}
        used_groups |= {b.get("topic") for b in analysis.get("sns_buzz_topics", []) if b.get("topic")}

        # 他セクションで既に触れた見出し・詳細文と重複する話題を除外するための正規化テキスト集合
        used_texts: set = set()
        for t in analysis.get("spotlight_topics", []):
            for field in ("headline", "detail"):
                if t.get(field):
                    used_texts.add(_norm_text(t[field]))
        for b in analysis.get("sns_buzz_topics", []):
            if b.get("description"):
                used_texts.add(_norm_text(b["description"]))
        for c in analysis.get("member_changes", []):
            if c.get("detail"):
                used_texts.add(_norm_text(c["detail"]))

        def _already_covered(title_norm: str) -> bool:
            return any(
                ut and (title_norm in ut or ut in title_norm)
                for ut in used_texts
            )

        seen_titles: set = set()
        picks: list[dict] = []
        for n in news:
            if n.get("category") != "general":
                continue
            title = n.get("title", "")
            if not title or title in seen_titles:
                continue
            group = n.get("group_name")
            if group and group in used_groups:
                continue
            if _already_covered(_norm_text(title)):
                continue
            seen_titles.add(title)
            picks.append(n)
            if len(picks) >= 5:
                break

        if not picks:
            return []

        result = [
            line(M, "このほかにも気になるニュースがあれば教えてください。"),
            line(R, "はい、ここでいくつかまとめてご紹介しますね。"),
        ]
        for n in picks:
            source = _source_label(n.get("source", ""))
            title = n.get("title", "")
            source_text = f"（{source}）" if source else ""
            result.append(line(R, f"「{title}」{source_text}。"))
        result.append(line(M, "話題は尽きませんね。引き続きチェックしていきましょう。"))
        return result

    # ── Upcoming Events ───────────────────────────────────────────────────

    def _upcoming_events(self, analysis: dict) -> list[str]:
        upcoming = analysis.get("upcoming_events", "")
        if not upcoming:
            return []
        return [
            line(M, "最後に、今後の注目情報があれば教えてください。"),
            line(R, upcoming),
            line(M, "楽しみですね。リョウさん、本日もありがとうございました。"),
            line(R, "ありがとうございました。皆さんも各グループの動向、ぜひチェックしてみてください。"),
        ]

    # ── Ending ────────────────────────────────────────────────────────────

    def _ending(self, date_jp: str) -> list[str]:
        return [
            line(M, f"以上、{date_jp}のライブアイドルデイリーをお届けしました。"),
            line(R, "情報の引用元はIDOL REPORT.com、Google Newsなどです。"),
            line(M, "各グループの最新情報は公式SNSや各メディアサイトでもご確認ください。"),
            line(R, "ライブアイドルデイリーは毎朝配信しています。購読登録もぜひよろしくお願いします。"),
            line(M, "本日もよい一日をお過ごしください。"),
            line(R, "それでは。"),
        ]
