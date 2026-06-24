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
            line(R, "おはようございます。リョウです。今日もライブアイドルシーンの最新情報をまとめてお伝えします。"),
            line(M, "本日は、前日から今朝にかけてのライブ情報、メンバー変動、解散情報をお届けします。"),
            line(M, "情報の引用元はIDOL REPORT.com、音楽ナタリー、Google Newsです。"),
            line(M, "それでは早速参りましょう。"),
        ]

    # ── Scene Summary ─────────────────────────────────────────────────────

    def _scene_summary(self, analysis: dict) -> list[str]:
        summary = analysis.get("scene_summary", "")
        if not summary:
            return []
        return [
            line(M, "まずは、ライブアイドルシーン全体の動向からです。リョウさん、いかがでしたか？"),
            line(R, summary),
        ]

    # ── Spotlight Topics ──────────────────────────────────────────────────

    def _spotlight_topics(self, analysis: dict) -> list[str]:
        topics = analysis.get("spotlight_topics", [])
        # member_changeは_member_changesセクションで扱うため重複を除外
        topics = [t for t in topics if t.get("category") != "member_change"]
        if not topics:
            return []

        lines = [line(M, "続きまして、直近の注目トピックをご紹介します。リョウさん、まず1つ目は？")]
        transitions = [
            (line(M, "詳しく教えてください。"), line(M, "2つ目はいかがでしょう？")),
            (line(M, "詳しく教えてください。"), line(M, "3つ目はどちらですか？")),
            (line(M, "詳しく教えてください。"), None),
        ]

        for i, topic in enumerate(topics[:3]):
            group = topic.get("group", "")
            headline = topic.get("headline", "")
            detail = topic.get("detail", "")
            source = _source_label(topic.get("source", ""))
            cat_label = CATEGORY_LABELS.get(topic.get("category", ""), "")

            ordinal = f"{i + 1}つ目"
            source_text = f"引用元は{source}です。" if source else ""

            if i == 0:
                lines.append(line(R, f"{ordinal}は「{group}」に関する{cat_label}の情報です。{headline}"))
                if detail:
                    lines.append(transitions[i][0])
                    lines.append(line(R, f"{detail}{source_text}"))
            else:
                prev_transition = transitions[i - 1][1]
                if prev_transition:
                    lines.append(prev_transition)
                lines.append(line(R, f"{ordinal}は「{group}」です。{headline}"))
                if detail:
                    lines.append(transitions[i][0])
                    lines.append(line(R, f"{detail}{source_text}"))

        lines.append(line(M, "ありがとうございます。気になる動きが続いていますね。"))
        return lines

    # ── Member Changes ────────────────────────────────────────────────────

    def _member_changes(self, analysis: dict, news: list[dict]) -> list[str]:
        changes = analysis.get("member_changes", [])

        # フォールバック: AIが返せなかった場合にニュースから直接生成
        if not changes:
            changes = self._extract_changes_from_news(news)

        if not changes:
            return []

        lines = [
            line(M, "続いて、直近のメンバー変動情報をまとめてお伝えします。"),
            line(R, "すべてIDOL REPORT.comの情報をもとにお伝えします。"),
        ]

        for ch in changes[:8]:
            group = ch.get("group", "")
            member = ch.get("member", "")
            change_type = CHANGE_TYPE_LABELS.get(ch.get("change_type", ""), ch.get("change_type", ""))
            scheduled = ch.get("scheduled_date", "")
            detail = ch.get("detail", "")

            member_text = f"の{member}さん" if member else ""
            date_text = f"（{scheduled}付）" if scheduled else ""

            lines.append(
                line(R, f"「{group}」{member_text}が{change_type}{date_text}。{detail}")
            )

        lines.append(line(M, "こうした情報は各グループの公式SNSでもご確認ください。"))
        return lines

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

        lines = [
            line(M, "続いて、SNSやウェブ上で特に話題になっているライブアイドル関連のトピックをご紹介します。"),
            line(R, "この日、注目を集めていた話題をピックアップしました。"),
        ]

        for i, topic in enumerate(buzz_topics[:3]):
            topic_name = topic.get("topic", "")
            description = topic.get("description", "")
            reason = topic.get("reason", "")

            if i == 0:
                lines.append(line(R, f"まず「{topic_name}」が話題になっています。{description}"))
            else:
                lines.append(line(R, f"また「{topic_name}」も注目されています。{description}"))

            if reason:
                lines.append(line(M, f"{reason}ということなんですね。"))

        lines.append(line(M, "SNSの盛り上がりも気になりますね。引き続きチェックしていきましょう。"))
        return lines

    # ── Upcoming Events ───────────────────────────────────────────────────

    def _upcoming_events(self, analysis: dict) -> list[str]:
        upcoming = analysis.get("upcoming_events", "")
        if not upcoming:
            return []
        return [
            line(M, "最後に、今後の注目情報を教えてください。"),
            line(R, upcoming),
            line(M, "リョウさん、本日もありがとうございました。"),
            line(R, "ありがとうございました。"),
        ]

    # ── Ending ────────────────────────────────────────────────────────────

    def _ending(self, date_jp: str) -> list[str]:
        return [
            line(M, f"以上、{date_jp}のライブアイドルデイリーをお届けしました。"),
            line(M, "情報の引用元はIDOL REPORT.com、音楽ナタリー、Google Newsです。"),
            line(M, "各グループの最新情報は公式SNSや各メディアサイトでご確認ください。"),
            line(M, "ライブアイドルデイリーは毎朝配信しています。購読登録もぜひどうぞ。"),
            line(M, "本日もよい一日をお過ごしください。"),
            line(R, "それでは。"),
        ]
