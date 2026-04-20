"""
slide_builder.py — v3（2段階アプローチ）

Phase 1: 全テキストからプレゼンのアウトラインを生成
  → 章構成・キーメッセージ・レイアウト方針を決定
Phase 2: アウトライン + 各スライドのテキストから詳細を生成
  → レイアウト・タイトル・サブテキスト・項目を生成

GPT を「分類器」ではなく「プレゼン著者」として使う。
"""
from __future__ import annotations
import json
from openai import AzureOpenAI
from config import (
    AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_API_VERSION, AZURE_OPENAI_DEPLOYMENT,
)

# ═══════════════════════════════════════════════════════
#  Phase 1: アウトライン生成
# ═══════════════════════════════════════════════════════

OUTLINE_SYSTEM = """\
あなたはプレゼンテーション構成の専門家です。
音声書き起こしテキストを読み、魅力的なプレゼンテーションのアウトラインを設計してください。

【あなたの仕事】
- 会話テキストの要点を抽出し、プレゼンとして再構成する
- 視覚的に伝わるスライド設計をする
- feature（用語解説）に頼らず、多様なレイアウトで情報を構造化する

【レイアウト一覧と使い所】
- title    : 動画のタイトル。最初のスライドのみ。
- section  : 章の区切り。大きな話題転換（全体で3〜6個）。
- question : 聞き手への問いかけ。各章の冒頭や転換点で使う。
- feature  : 1つの概念の深掘り。タイトル＋説明文。★最も制限すべきレイアウト★
- split    : 2つの概念の比較・対比。items に2項目。
- flow     : 手順・プロセス。items に3〜4項目。
- bullets  : 要点の列挙。items に3〜4項目。
- cards    : 並列する複数概念の一覧。items に3〜6項目（「名前: 説明」形式。番号は不要）。
- timeline : 時系列。年号・日付がある場合。items に2〜4項目。

═══ 変換パターン（必ず適用）═══

1. 「AとBの違い」「一方〜他方〜」「逆に」→ split
2. 「3つの○○」「ポイントは〜」「まとめると」→ bullets
3. 「まず〜次に〜最後に」「ステップ」「手順」→ flow
4. 並列する概念（営業CF・投資CF・財務CFなど）→ cards（1枚にまとめる）
5. 「〜とは？」「なぜ〜？」「どうすれば〜？」→ question
6. 会話の「問い→答え」パターン → question + bullets/split
7. 章の最後のまとめ → bullets
8. 上記のいずれにも該当しない場合のみ → feature

═══ 数量制限（厳守）═══

- feature は全体の20%以下（40枚なら8枚以下）
- feature の連続は最大2回まで（3連続は禁止）
- question は全体の15〜20%（各章に1〜2個）
- split + bullets + flow + cards で全体の40%以上
- flow は全体で2〜4個（手順があれば積極的に使う）
- cards は並列概念があれば使う（例: 3つのCF分類 → cards 1枚）
- section は全体で4〜6個

═══ 構成テンプレート（各章の推奨パターン）═══

典型的な章の構成:
  section → question → split/bullets → feature → bullets/flow

まとめ・エンディングの構成:
  section → bullets（振り返り） → question（未来への問い） → bullets（結論）

避けるべき構成:
  section → feature → feature → feature → feature（単調）
  bullets → feature → feature → feature（まとめ後に用語解説は不自然）

═══ レイアウト多様性チェック ═══

最終チェックとして、以下を確認してください:
- flow が0個 → 手順やプロセスを含むスライドを1つ以上 flow にする
- cards が0個 → 並列する3つ以上の概念があれば cards にする
- 最後の5スライドに feature が3個以上 → bullets/question に変換する

═══ 出力形式 ═══
JSON配列のみ:
[{
  "slide_index": 0,
  "layout": "title",
  "key_message": "伝えるべき核心メッセージ（1文）",
  "section_name": "属する章名"
}, ...]"""

OUTLINE_USER = """\
以下は音声書き起こしテキストをスライド単位にまとめたものです。
{count}枚のスライドについて、プレゼンのアウトラインを設計してください。

{slides_text}"""


# ═══════════════════════════════════════════════════════
#  Phase 2: スライド詳細生成
# ═══════════════════════════════════════════════════════

DETAIL_SYSTEM = """\
あなたはプレゼンテーションのスライドデザイナーです。
アウトラインに基づいて、各スライドの詳細コンテンツを設計してください。

【レイアウト別の設計ルール】

■ title: icon + title(20字以内) + subtitle(30字以内)
■ section: icon + title(20字以内) + subtitle(30字以内)
■ question: icon + title(疑問形、20字以内) + items(要点を3項目)
■ feature: icon + title(20字以内) + subtitle(30字以内、★必須★)
■ split: icon + title(20字以内) + items(2項目: 「A概念」「B概念」)
■ flow: icon + title(20字以内) + items(3〜4項目: 各ステップ)
■ bullets: icon + title(20字以内) + items(3〜4項目: 要点)
■ cards: icon + title(20字以内) + items(3〜6項目: 「名前: 説明」形式、番号は自動付与)
■ timeline: icon + title(20字以内) + items(2〜4項目: 「年: 説明」形式)

【items の書き方】
- 各項目は15字以内で体言止め
- 会話テキストから核心を抽出して簡潔に表現する
- split: 対比する2つの概念を明確に（例: 「利益：帳簿上の数字」「現金：支払いの現実」）
- bullets: 並列する要点を3〜4個（例: 「営業CF」「投資CF」「財務CF」）
- flow: 手順を時系列で3〜4個（例: 「日次確認」→「月次集計」→「中長期予測」）
- cards: 概念を一覧化（例: 「営業活動CF: 本業で得たお金の流れ」「投資活動CF: 設備投資の流れ」「財務活動CF: 資金調達の流れ」）※番号は付けない

【テキスト変換ルール】
- 話し言葉のフィラー（「ええ」「はい」「なるほど」）は完全除去
- 会話の掛け合いから要点だけを抽出する
- titleは体言止めで力強く表現する
- icon は内容を象徴する絵文字1つ

【出力】JSON配列のみ:
[{"id": 0, "layout": "split", "title": "利益と現金の違い", "subtitle": "", "items": ["利益：帳簿上の計算", "現金：実際の支払い能力"], "icon": "⚖️"}, ...]"""


# ═══════════════════════════════════════════════════════
#  アウトライン自動修正
# ═══════════════════════════════════════════════════════

def _fix_outline_variety(outline: list[dict], segments: list[dict]) -> list[dict]:
    """
    feature が多すぎる場合、テキストのパターンに基づいて
    他のレイアウトに自動変換する。
    """
    n = len(outline)

    for i, o in enumerate(outline):
        if o.get("layout") != "feature":
            continue

        text = segments[i]["text"] if i < len(segments) else ""

        # 連続 feature のカウント
        streak = 1
        if i >= 2 and outline[i-1].get("layout") == "feature" and outline[i-2].get("layout") == "feature":
            streak = 3
        elif i >= 1 and outline[i-1].get("layout") == "feature":
            streak = 2

        new_layout = None

        # 比較・対比パターン
        if any(w in text for w in ["一方", "逆に", "対して", "違い", "ではなく", "と比べ"]):
            new_layout = "split"
        # 列挙パターン
        elif any(w in text for w in ["3つ", "4つ", "ポイント", "まとめ", "振り返"]):
            new_layout = "bullets"
        # 手順パターン
        elif any(w in text for w in ["まず", "次に", "最後に", "ステップ", "1つ目"]):
            new_layout = "flow"
        # 疑問パターン
        elif "？" in text[:80] or "?" in text[:80]:
            new_layout = "question"
        # 末尾5スライドの feature → bullets に
        elif i >= n - 5:
            new_layout = "bullets"
        # 3連続以上 → bullets に
        elif streak >= 3:
            new_layout = "bullets"

        if new_layout:
            o["layout"] = new_layout

    # 多様性チェック: flow が0なら手順っぽいものを変換
    from collections import Counter
    layout_counts = Counter(o.get("layout") for o in outline)

    if layout_counts.get("flow", 0) == 0:
        for i, o in enumerate(outline):
            if o.get("layout") in ("feature", "bullets"):
                text = segments[i]["text"] if i < len(segments) else ""
                if any(w in text for w in ["まず", "次に", "手順", "ステップ", "段階", "プロセス", "時間軸"]):
                    o["layout"] = "flow"
                    print(f"[Slide] 多様性修正: #{i+1} → flow")
                    break

    if layout_counts.get("cards", 0) == 0:
        for i, o in enumerate(outline):
            if o.get("layout") in ("feature", "bullets"):
                text = segments[i]["text"] if i < len(segments) else ""
                if any(w in text for w in ["分類", "3つの", "3区分", "種類"]):
                    o["layout"] = "cards"
                    print(f"[Slide] 多様性修正: #{i+1} → cards")
                    break

    # 最後のスライドは question（余韻を残す問いかけ）
    if n > 0 and outline[-1].get("layout") != "question":
        old = outline[-1].get("layout", "?")
        outline[-1]["layout"] = "question"
        print(f"[Slide] エンディング修正: #{n} {old} → question")

    return outline


# ═══════════════════════════════════════════════════════
#  バリデーション
# ═══════════════════════════════════════════════════════

VALID_LAYOUTS = {"title", "question", "section", "feature",
                 "split", "flow", "timeline", "bullets", "cards"}

ITEMS_MIN = {"split": 2, "flow": 3, "bullets": 3, "cards": 3, "timeline": 2}

DEFAULT_ICONS = {
    "title": "📋", "question": "❓", "section": "📖",
    "feature": "💡", "split": "⚖️", "flow": "🔄",
    "timeline": "📅", "bullets": "📝", "cards": "🗂️",
}


def _validate(slide: dict, original_text: str) -> dict:
    layout = slide.get("layout", "feature")
    if layout not in VALID_LAYOUTS:
        layout = "feature"
    slide["layout"] = layout

    title = slide.get("title", "")
    if len(title) > 20:
        for i in range(19, 9, -1):
            if title[i] in "。、・ ":
                title = title[:i]
                break
        else:
            title = title[:20]
    if not title:
        title = original_text[:20].strip()
    slide["title"] = title

    sub = slide.get("subtitle", "")
    if len(sub) > 30:
        sub = sub[:30]
    slide["subtitle"] = sub

    items = slide.get("items", [])
    if not isinstance(items, list):
        items = []
    items = [str(it)[:15] for it in items if str(it).strip()]
    slide["items"] = items

    min_items = ITEMS_MIN.get(layout, 0)
    if min_items > 0 and len(items) == 0:
        slide["layout"] = "feature"
        if not slide["subtitle"] and len(original_text) > 20:
            slide["subtitle"] = original_text[20:50].strip()

    if not slide.get("icon"):
        slide["icon"] = DEFAULT_ICONS.get(slide["layout"], "💡")

    return slide


# ═══════════════════════════════════════════════════════
#  メイン処理
# ═══════════════════════════════════════════════════════

def build_slides(segments: list[dict]) -> list[dict]:
    client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION,
    )

    # ── Phase 1: アウトライン生成 ─────────────────────────
    print("[Slide] Phase 1: アウトライン生成中...")
    outline = _generate_outline(client, segments)
    print(f"[Slide] アウトライン完了: {len(outline)} スライド分")

    # アウトライン制約チェック＋自動修正
    from collections import Counter
    ol_layouts = Counter(o.get("layout", "?") for o in outline)
    feat_count = ol_layouts.get("feature", 0)
    feat_pct = feat_count / max(len(outline), 1) * 100

    print(f"[Slide] アウトライン レイアウト: {dict(ol_layouts.most_common())}")
    print(f"[Slide] feature率: {feat_pct:.0f}% ({feat_count}/{len(outline)})")

    if feat_pct > 25:
        print(f"[Slide] ⚠️ feature率が高い — 連続 feature を自動変換します")
        outline = _fix_outline_variety(outline, segments)
        ol_layouts = Counter(o.get("layout", "?") for o in outline)
        feat_count = ol_layouts.get("feature", 0)
        print(f"[Slide] 修正後: {dict(ol_layouts.most_common())} (feature {feat_count}/{len(outline)})")

    # ── Phase 2: スライド詳細生成 ─────────────────────────
    print("[Slide] Phase 2: スライド詳細生成中...")
    details = _generate_details(client, segments, outline)

    # ── マージ＋バリデーション ─────────────────────────────
    output = []
    for i, seg in enumerate(segments):
        s = dict(seg)
        slide = details.get(i, {
            "layout": "feature", "title": seg["text"][:20],
            "subtitle": "", "items": [], "icon": "",
        })
        slide = _validate(slide, seg.get("text", ""))

        s["slide_layout"] = slide["layout"]
        s["slide_title"]  = slide["title"]
        s["slide_sub"]    = slide["subtitle"]
        s["slide_items"]  = slide["items"]
        s["slide_icon"]   = slide["icon"]
        s["slide_number"] = slide.get("number", "")
        output.append(s)

    # section 連番
    n = 0
    for s in output:
        if s["slide_layout"] == "section":
            n += 1
            s["slide_number"] = str(n).zfill(2)

    # 連続 feature 警告
    streak = 1
    for i in range(1, len(output)):
        if output[i]["slide_layout"] == output[i-1]["slide_layout"] == "feature":
            streak += 1
            if streak == 4:
                print(f"[Slide] ⚠️  feature 4連続 (#{i-2}〜#{i+1})")
        else:
            streak = 1

    # サマリー
    layout_dist = Counter(s["slide_layout"] for s in output)
    print(f"[Slide] 完了: {len(output)} スライド")
    print(f"[Slide] レイアウト: {dict(layout_dist.most_common())}")
    for s in output[:5]:
        print(f"  [{s['slide_layout']:9}] {s['slide_title'][:25]}")

    return output


def _generate_outline(client, segments: list[dict]) -> list[dict]:
    """Phase 1: 全テキストからアウトラインを生成"""

    # スライドテキストをコンパクトに整形
    lines = []
    for i, s in enumerate(segments):
        text = s["text"][:200]
        if len(s["text"]) > 200:
            text += "…"
        spk = s.get("speaker", "?")
        dur = s.get("duration_ms", 0) / 1000
        lines.append(f"[#{i}] ({dur:.0f}秒, 話者{spk}) {text}")

    slides_text = "\n".join(lines)

    resp = client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT,
        messages=[
            {"role": "system", "content": OUTLINE_SYSTEM},
            {"role": "user", "content": OUTLINE_USER.format(
                count=len(segments), slides_text=slides_text
            )},
        ],
        temperature=0.3,
        max_tokens=4096,
    )

    raw = resp.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        outline = json.loads(raw)
        if isinstance(outline, list):
            return outline
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[Slide] アウトライン パースエラー: {e}")

    # フォールバック: 全部 feature
    return [{"slide_index": i, "layout": "feature",
             "key_message": "", "section_name": ""}
            for i in range(len(segments))]


def _generate_details(client, segments: list[dict], outline: list[dict]) -> dict:
    """Phase 2: アウトラインに基づいてスライド詳細を生成"""

    # outline を index で引けるように
    outline_map = {}
    for o in outline:
        idx = o.get("slide_index", -1)
        if 0 <= idx < len(segments):
            outline_map[idx] = o

    # チャンクごとに生成
    chunk_size = 12
    results: dict[int, dict] = {}

    for start in range(0, len(segments), chunk_size):
        chunk_end = min(start + chunk_size, len(segments))
        print(f"[Slide]   詳細生成中... ({start+1}〜{chunk_end}/{len(segments)})")

        payload = []
        for i in range(start, chunk_end):
            seg = segments[i]
            ol = outline_map.get(i, {})
            text = seg["text"][:250]
            if len(seg["text"]) > 250:
                text += "…"
            payload.append({
                "id": i,
                "text": text,
                "layout": ol.get("layout", "feature"),
                "key_message": ol.get("key_message", ""),
                "section_name": ol.get("section_name", ""),
            })

        resp = client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": DETAIL_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.2,
            max_tokens=4096,
        )

        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        try:
            items = json.loads(raw)
            for item in items:
                results[item["id"]] = item
        except (json.JSONDecodeError, KeyError) as e:
            print(f"[Slide]   パースエラー: {e} — フォールバック")
            for i in range(start, chunk_end):
                seg = segments[i]
                results[i] = {
                    "id": i, "layout": "feature",
                    "title": seg["text"][:20],
                    "subtitle": seg["text"][20:50] if len(seg["text"]) > 20 else "",
                    "items": [], "icon": "",
                }

    return results


def save_slides(segments: list[dict], path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False, indent=2)
    print(f"[Slide] 保存: {path}")
