import json
import logging
import os
import time

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT_TEMPLATE = """あなたはライブアイドルシーンの専門ライターです。
以下のニュースデータを分析して、Podcastの素材となる情報を日本語で生成してください。

【注意事項】
- 入力データに含まれる情報のみを使用すること（推測・補完は禁止）
- グループ名・メンバー名・日付・会場は原文のまま使用すること
- 引用元が明記できない情報は「〜とみられる」等で推測であることを明示すること
- 全フィールドは必須です。空文字・nullは使用しないこと

【入力データ】
対象日付: {date}

■ ライブレポート記事（{live_count}件）
{live_reports}

■ メンバー変動記事（{member_count}件）
{member_changes}

■ 解散・活動終了記事（{disband_count}件）
{disbandments}

■ その他ニュース（{general_count}件）
{general_news}

【出力形式】
以下のJSON形式で厳密に出力してください：

{{
  "scene_summary": "ライブアイドルシーン全体の動向を200〜300文字でまとめたナレーション（必須）",
  "spotlight_topics": [
    {{
      "group": "グループ名（入力データに存在するもののみ）",
      "category": "live_report または member_change または disbandment",
      "headline": "見出し（40文字以内）",
      "detail": "詳細説明（100〜200文字・入力データの事実のみ）",
      "source": "引用元メディア名"
    }}
  ],
  "member_changes": [
    {{
      "group": "グループ名",
      "member": "メンバー名",
      "change_type": "卒業 または 脱退 または 加入 または 解雇 または 活動終了",
      "scheduled_date": "予定日（YYYY-MM-DD形式、不明な場合は空文字）",
      "detail": "詳細（50文字以内）"
    }}
  ],
  "upcoming_events": "今後の注目ライブ・イベント情報（100〜200文字・入力データに含まれるもののみ）",
  "trending_themes": ["テーマ1", "テーマ2", "テーマ3"],
  "keywords": ["キーワード1", "キーワード2", "キーワード3", "キーワード4", "キーワード5"]
}}

spotlight_topicsは最大3件。データが少ない場合はその分だけ出力してください。
member_changesはデータに含まれる全件を出力してください。
JSONのみを出力し、マークダウンやコードブロックは使用しないこと。"""


class AIAnalyzer:
    def __init__(self, settings: dict):
        self.settings = settings
        self.provider = os.getenv("AI_PROVIDER", "openrouter")
        self.max_retries = settings.get("ai", {}).get("max_retries", 3)
        self.retry_delay = settings.get("ai", {}).get("retry_delay", 5)

    def analyze(self, target_date: str, news: list[dict]) -> dict:
        prompt = self._build_prompt(target_date, news)
        for attempt in range(self.max_retries):
            try:
                if self.provider == "gemini":
                    result = self._call_gemini(prompt)
                else:
                    result = self._call_openrouter(prompt)

                parsed = self._parse_response(result)
                parsed["raw_prompt"] = prompt[:2000]
                parsed["model_used"] = self._get_model_name()

                fallback = self._fallback_analysis(target_date, news)
                for key in ("scene_summary", "upcoming_events"):
                    if not parsed.get(key):
                        logger.warning(f"AI が {key} を返さなかったためフォールバックで補完")
                        parsed[key] = fallback[key]
                if not parsed.get("trending_themes"):
                    parsed["trending_themes"] = fallback.get("trending_themes", [])

                return parsed

            except Exception as e:
                logger.warning(f"AI分析失敗 (試行{attempt + 1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))

        logger.error("AI分析全試行失敗 - フォールバック分析を使用")
        return self._fallback_analysis(target_date, news)

    def _build_prompt(self, target_date: str, news: list[dict]) -> str:
        live_reports   = [n for n in news if n.get("category") == "live_report"]
        member_changes = [n for n in news if n.get("category") == "member_change"]
        disbandments   = [n for n in news if n.get("category") == "disbandment"]
        general        = [n for n in news if n.get("category") == "general"]

        def fmt(articles: list[dict]) -> str:
            if not articles:
                return "  （なし）"
            return "\n".join(
                f"  [{i+1}] {a['title']}（{a['source']}）"
                for i, a in enumerate(articles[:15])
            )

        return ANALYSIS_PROMPT_TEMPLATE.format(
            date=target_date,
            live_count=len(live_reports),
            member_count=len(member_changes),
            disband_count=len(disbandments),
            general_count=len(general),
            live_reports=fmt(live_reports),
            member_changes=fmt(member_changes),
            disbandments=fmt(disbandments),
            general_news=fmt(general),
        )

    OPENROUTER_FREE_MODELS = [
        "google/gemma-3-27b-it:free",
        "meta-llama/llama-3.1-8b-instruct:free",
        "google/gemma-2-9b-it:free",
        "mistralai/mistral-7b-instruct:free",
        "deepseek/deepseek-r1:free",
    ]

    def _call_openrouter(self, prompt: str) -> str:
        from openai import OpenAI
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY が設定されていません")

        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            default_headers={
                "HTTP-Referer": "https://github.com/idol-podcast",
                "X-Title": "Idol Podcast",
            },
        )
        primary = os.getenv("OPENROUTER_MODEL", "")
        candidates = (
            [primary] + [m for m in self.OPENROUTER_FREE_MODELS if m != primary]
            if primary else self.OPENROUTER_FREE_MODELS
        )
        last_error = None
        for model in candidates:
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=self.settings.get("ai", {}).get("max_tokens", 4000),
                    temperature=self.settings.get("ai", {}).get("temperature", 0.3),
                )
                self._used_model = model
                logger.info(f"OpenRouter応答取得 (model={model})")
                return response.choices[0].message.content
            except Exception as e:
                err_str = str(e)
                if any(k in err_str for k in ("404", "unavailable", "free")):
                    logger.warning(f"モデル {model} 利用不可 - 次を試行")
                    last_error = e
                    continue
                raise
        raise RuntimeError(f"全フォールバックモデル試行失敗: {last_error}")

    def _call_gemini(self, prompt: str) -> str:
        import google.generativeai as genai
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY が設定されていません")
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(prompt)
        logger.info("Gemini応答取得")
        return response.text

    def _get_model_name(self) -> str:
        if self.provider == "gemini":
            return "gemini-1.5-flash"
        return getattr(self, "_used_model", os.getenv("OPENROUTER_MODEL", "openrouter-free"))

    def _parse_response(self, response_text: str) -> dict:
        text = response_text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            text = text[start:end]
        data = json.loads(text)
        for key in ("spotlight_topics", "member_changes", "trending_themes", "keywords"):
            if key not in data:
                data[key] = []
        for key in ("scene_summary", "upcoming_events"):
            if key not in data:
                data[key] = ""
        return data

    def _fallback_analysis(self, target_date: str, news: list[dict]) -> dict:
        live_reports   = [n for n in news if n.get("category") == "live_report"]
        member_changes = [n for n in news if n.get("category") == "member_change"]
        disbandments   = [n for n in news if n.get("category") == "disbandment"]

        parts = []
        if disbandments:
            parts.append(f"解散・活動終了の発表が{len(disbandments)}件確認されています。")
        if member_changes:
            parts.append(f"メンバー変動の発表が{len(member_changes)}件ありました。")
        if live_reports:
            parts.append(f"ライブレポートが{len(live_reports)}件掲載されています。")
        if not parts:
            parts.append("本日はライブアイドルシーンの最新情報をお届けします。")

        scene_summary = "".join(parts)

        spotlight = []
        for n in (live_reports + disbandments + member_changes)[:3]:
            spotlight.append({
                "group": n.get("group_name") or "不明",
                "category": n.get("category", "general"),
                "headline": n.get("title", "")[:40],
                "detail": n.get("summary", n.get("title", ""))[:200],
                "source": n.get("source", ""),
            })

        changes = []
        for n in member_changes[:10]:
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

        upcoming_parts = []
        for n in live_reports[:2]:
            upcoming_parts.append(n.get("title", "")[:60])
        upcoming_events = "、".join(upcoming_parts) if upcoming_parts else \
            "引き続き各グループの公式SNSや専門メディアで最新情報をご確認ください。"

        return {
            "scene_summary": scene_summary,
            "spotlight_topics": spotlight,
            "member_changes": changes,
            "upcoming_events": upcoming_events,
            "trending_themes": [],
            "keywords": [],
            "model_used": "fallback",
            "raw_prompt": "",
        }
