import json
import logging
import os
import time

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT_TEMPLATE = """あなたはライブアイドルシーンの専門ライターです。
以下のニュースデータを分析して、Podcastの素材となる情報を日本語で生成してください。
このPodcastは1エピソード5〜10分程度を想定しているため、入力データにある実際の情報は
できるだけ幅広く・漏れなく取り上げてください（同一グループの重複は避けつつ、件数の上限までしっかり埋めること）。
また、同じニュース記事・SNS投稿・出来事・グループ名を spotlight_topics / member_changes /
sns_buzz_topics / upcoming_events / trending_themes など複数のフィールドで重複して
取り上げないこと。1つの話題は最も適切なフィールド1箇所にのみ入れること
（例: spotlight_topicsで取り上げたグループの話題を、sns_buzz_topicsで再度取り上げない）。

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

■ SNSバズ・話題のトピック（{sns_count}件）
{sns_trending}

■ 注目ライブ・イベント（{upcoming_count}件、大型会場開催またはメンバー動向系のみ。当日開催分を優先、無ければ今後1週間分）
{upcoming_live_events}

■ その他ニュース（{general_count}件）
{general_news}

【出力形式】
以下のJSON形式で厳密に出力してください：

{{
  "scene_summary": "本日扱うテーマを一行未満ずつの短い言及で列挙する概要ナレーション（120〜180文字程度。各テーマの詳細説明は後続セクションで話すため、ここでは深掘りせず簡潔に触れるだけにすること）",
  "spotlight_topics": [
    {{
      "group": "グループ名（入力データに存在するもののみ）",
      "category": "live_report または disbandment のみ（member_changeは絶対に含めないこと。メンバー変動はmember_changesフィールドで扱う）",
      "headline": "見出し（40文字以内）",
      "detail": "詳細説明（150〜250文字・入力データの事実のみ）",
      "source": "引用元メディア名"
    }}
  ],
  "member_changes": [
    {{
      "group": "グループ名",
      "member": "メンバー名（不明な場合は空文字）",
      "change_type": "卒業 または 脱退 または 加入 または 解雇 または 活動終了",
      "scheduled_date": "予定日（YYYY-MM-DD形式、不明な場合は空文字）",
      "detail": "詳細（50文字以内）"
    }}
  ],
  "sns_buzz_topics": [
    {{
      "topic": "話題のトピック・グループ名または出来事名",
      "description": "SNSやメディアで話題になっている具体的な内容の詳細説明（150〜250文字・入力データの事実のみ。誰が何をした/何が起きたのかが聞いただけで分かるレベルまで具体的に書くこと）",
      "reason": "なぜ話題か・反響の大きさが分かる情報（いいね数やRT数があれば含める。40文字以内）"
    }}
  ],
  "upcoming_events": "今後の注目ライブ・イベント情報（150〜300文字・「注目ライブ・イベント」のデータを優先して使用し、複数件あれば列挙する）",
  "trending_themes": ["テーマ1", "テーマ2", "テーマ3"],
  "keywords": ["キーワード1", "キーワード2", "キーワード3", "キーワード4", "キーワード5"]
}}

spotlight_topicsは最大5件。live_reportとdisbandmentのみ対象（member_changeは含めないこと）。該当データがあるだけ拾い上げ、上限まで埋めること。データが本当に少ない場合のみその分だけ出力してください。
sns_buzz_topicsは最大4件。SNSバズ・話題データがない場合は空配列でよい。
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

        providers = self._build_provider_order()
        for provider_name, caller in providers:
            for attempt in range(self.max_retries):
                try:
                    result = caller(prompt)
                    parsed = self._parse_response(result)
                    parsed["raw_prompt"] = prompt[:2000]
                    parsed["model_used"] = self._get_model_name()

                    ai_fallback = self._fallback_analysis(target_date, news)
                    for key in ("scene_summary", "upcoming_events"):
                        if not parsed.get(key):
                            logger.warning(f"AI が {key} を返さなかったためフォールバックで補完")
                            parsed[key] = ai_fallback[key]
                    if not parsed.get("trending_themes"):
                        parsed["trending_themes"] = ai_fallback.get("trending_themes", [])

                    return self._dedupe_analysis(parsed)

                except Exception as e:
                    logger.warning(f"AI分析失敗 [{provider_name}] (試行{attempt + 1}/{self.max_retries}): {e}")
                    if attempt < self.max_retries - 1:
                        time.sleep(self.retry_delay * (attempt + 1))

            logger.warning(f"{provider_name} 全試行失敗 - 次のプロバイダーを試行")

        logger.error("全プロバイダー失敗 - フォールバック分析を使用")
        return self._dedupe_analysis(self._fallback_analysis(target_date, news))

    @staticmethod
    def _dedupe_analysis(analysis: dict) -> dict:
        """AIが同じ話題を複数件で重複して返すことがあるため、各リスト内の重複を除去する。"""
        def norm(s: str) -> str:
            return "".join((s or "").split())

        seen_spotlight: set = set()
        spotlight = []
        for t in analysis.get("spotlight_topics") or []:
            key = (t.get("group", ""), norm(t.get("headline", "")))
            if key in seen_spotlight:
                continue
            seen_spotlight.add(key)
            spotlight.append(t)
        analysis["spotlight_topics"] = spotlight

        seen_changes: set = set()
        changes = []
        for c in analysis.get("member_changes") or []:
            key = (c.get("group", ""), c.get("member", ""), c.get("change_type", ""), norm(c.get("detail", "")))
            if key in seen_changes:
                continue
            seen_changes.add(key)
            changes.append(c)
        analysis["member_changes"] = changes

        seen_buzz: set = set()
        buzz = []
        for b in analysis.get("sns_buzz_topics") or []:
            key = norm(b.get("topic", ""))
            if key in seen_buzz:
                continue
            seen_buzz.add(key)
            buzz.append(b)

        # セクションをまたいだ重複除去: spotlight_topics/member_changesで
        # 既に扱ったグループ・内容をsns_buzz_topicsから除外する
        covered_groups = {norm(t.get("group", "")) for t in analysis["spotlight_topics"] if t.get("group")}
        covered_groups |= {norm(c.get("group", "")) for c in analysis["member_changes"] if c.get("group")}

        covered_texts: set = set()
        for t in analysis["spotlight_topics"]:
            for field in ("headline", "detail"):
                if t.get(field):
                    covered_texts.add(norm(t[field]))
        for c in analysis["member_changes"]:
            if c.get("detail"):
                covered_texts.add(norm(c["detail"]))

        def covered(text: str) -> bool:
            return bool(text) and any(ct and (text in ct or ct in text) for ct in covered_texts)

        buzz = [
            b for b in buzz
            if norm(b.get("topic", "")) not in covered_groups
            and not covered(norm(b.get("description", "")))
        ]
        analysis["sns_buzz_topics"] = buzz

        return analysis

    def _build_provider_order(self) -> list:
        """プロバイダーの試行順序を返す。設定プロバイダーを先頭に、他は利用可能ならフォールバックに追加。"""
        callers = {
            "groq": ("Groq", self._call_groq, "GROQ_API_KEY"),
            "gemini": ("Gemini", self._call_gemini, "GEMINI_API_KEY"),
            "openrouter": ("OpenRouter", self._call_openrouter, "OPENROUTER_API_KEY"),
            "grok": ("Grok", self._call_grok, "GROK_API_KEY"),
        }

        default_order = ("groq", "gemini", "openrouter", "grok")  # 無料 → 無料 → 無料(不安定) → 有料
        primary = self.provider if self.provider in callers else "groq"
        order_keys = [primary] + [k for k in default_order if k != primary]

        order = []
        for key in order_keys:
            name, caller, env_key = callers[key]
            if key == primary or os.getenv(env_key):
                order.append((name, caller))
        return order

    def _build_prompt(self, target_date: str, news: list[dict]) -> str:
        live_reports   = [n for n in news if n.get("category") == "live_report"]
        member_changes = [n for n in news if n.get("category") == "member_change"]
        disbandments   = [n for n in news if n.get("category") == "disbandment"]
        sns_trending   = [n for n in news if n.get("category") == "sns_trending"]
        upcoming_live_events = [n for n in news if n.get("category") == "upcoming_live_event"]
        general        = [n for n in news if n.get("category") == "general"]

        def fmt(articles: list[dict]) -> str:
            if not articles:
                return "  （なし）"
            lines = []
            for i, a in enumerate(articles[:25]):
                summary = a.get("summary", "")
                summary_text = f"\n    概要: {summary[:120]}" if summary else ""
                lines.append(f"  [{i+1}] {a['title']}（{a['source']}）{summary_text}")
            return "\n".join(lines)

        return ANALYSIS_PROMPT_TEMPLATE.format(
            date=target_date,
            live_count=len(live_reports),
            member_count=len(member_changes),
            disband_count=len(disbandments),
            sns_count=len(sns_trending),
            upcoming_count=len(upcoming_live_events),
            general_count=len(general),
            live_reports=fmt(live_reports),
            member_changes=fmt(member_changes),
            disbandments=fmt(disbandments),
            sns_trending=fmt(sns_trending),
            upcoming_live_events=fmt(upcoming_live_events),
            general_news=fmt(general),
        )

    OPENROUTER_FREE_MODELS = [
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "poolside/laguna-m.1:free",
        "poolside/laguna-xs.2:free",
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

    GROQ_MODEL = "llama-3.3-70b-versatile"

    def _call_groq(self, prompt: str) -> str:
        from openai import OpenAI
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY が設定されていません")

        client = OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=api_key,
        )
        model = os.getenv("GROQ_MODEL", self.GROQ_MODEL)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=self.settings.get("ai", {}).get("max_tokens", 4000),
            temperature=self.settings.get("ai", {}).get("temperature", 0.3),
        )
        self._used_model = model
        logger.info(f"Groq応答取得 (model={model})")
        return response.choices[0].message.content

    GROK_MODEL = "grok-4-fast"

    def _call_grok(self, prompt: str) -> str:
        from openai import OpenAI
        api_key = os.getenv("GROK_API_KEY")
        if not api_key:
            raise ValueError("GROK_API_KEY が設定されていません")

        client = OpenAI(
            base_url="https://api.x.ai/v1",
            api_key=api_key,
        )
        model = os.getenv("GROK_MODEL", self.GROK_MODEL)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=self.settings.get("ai", {}).get("max_tokens", 4000),
            temperature=self.settings.get("ai", {}).get("temperature", 0.3),
        )
        self._used_model = model
        logger.info(f"Grok応答取得 (model={model})")
        return response.choices[0].message.content

    GEMINI_MODEL = "gemini-2.0-flash"

    def _call_gemini(self, prompt: str) -> str:
        import google.generativeai as genai
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY が設定されていません")
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(self.GEMINI_MODEL)
        response = model.generate_content(prompt)
        logger.info(f"Gemini応答取得 (model={self.GEMINI_MODEL})")
        return response.text

    def _get_model_name(self) -> str:
        if self.provider == "gemini":
            return self.GEMINI_MODEL
        if hasattr(self, "_used_model"):
            return self._used_model
        if self.provider == "groq":
            return os.getenv("GROQ_MODEL", self.GROQ_MODEL)
        if self.provider == "grok":
            return os.getenv("GROK_MODEL", self.GROK_MODEL)
        return os.getenv("OPENROUTER_MODEL", "openrouter-free")

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
        for key in ("spotlight_topics", "member_changes", "trending_themes", "keywords", "sns_buzz_topics"):
            if key not in data:
                data[key] = []
        # spotlight_topicsからmember_changeを除外（member_changesと重複するため）
        data["spotlight_topics"] = [
            t for t in data["spotlight_topics"]
            if t.get("category") != "member_change"
        ]
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

        # spotlight_topicsはlive_reportとdisbandmentのみ（member_changeは除外）
        spotlight = []
        for n in (live_reports + disbandments)[:3]:
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

        upcoming_live_events = [n for n in news if n.get("category") == "upcoming_live_event"]
        upcoming_parts = []
        for n in upcoming_live_events[:5]:
            upcoming_parts.append(n.get("title", "")[:80])
        if not upcoming_parts:
            for n in live_reports[:2]:
                upcoming_parts.append(n.get("title", "")[:60])
        upcoming_events = "、".join(upcoming_parts) if upcoming_parts else \
            "引き続き各グループの公式SNSや専門メディアで最新情報をご確認ください。"

        sns_trending = [n for n in news if n.get("category") == "sns_trending"]
        sns_buzz = []
        for n in sns_trending[:3]:
            title = n.get("title", "")
            summary = n.get("summary", "")
            group = n.get("group_name")
            # group_nameは記事見出し全体の誤抽出やX投稿の検索クエリがそのまま
            # 入っているケースがあるため、固有名詞らしい短さの場合のみ採用する
            is_real_group = bool(group) and len(group) <= 15
            topic = group if is_real_group else (title[:20] or "話題")
            # descriptionは実際の本文（タイトル）を優先し、いいね数などの
            # 数値情報だけになってしまわないようにする
            description = title[:200] if title else summary[:200]
            reason = summary[:40] if summary and summary != description else "SNSで話題"
            sns_buzz.append({
                "topic": topic,
                "description": description,
                "reason": reason,
            })

        return {
            "scene_summary": scene_summary,
            "spotlight_topics": spotlight,
            "member_changes": changes,
            "sns_buzz_topics": sns_buzz,
            "upcoming_events": upcoming_events,
            "trending_themes": [],
            "keywords": [],
            "model_used": "fallback",
            "raw_prompt": "",
        }
