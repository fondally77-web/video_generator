"""
corrector.py
Azure OpenAI で以下を1回の API 呼び出しで同時実施:
  1. Whisper の誤字脱字・読み間違いを修正
  2. 会話の話者を推定（A / B / unknown を付与）

タイムスタンプは維持し、speaker フィールドを追加して返す。
"""
from __future__ import annotations
import json
from openai import AzureOpenAI
from typing import TypedDict

from config import (
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_DEPLOYMENT,
)
from core.transcriber import Segment


# ── 話者付きセグメント型 ───────────────────────────────
class AnnotatedSegment(TypedDict):
    start:   float
    end:     float
    text:    str
    speaker: str   # "A" / "B" / "unknown"


# ── システムプロンプト ────────────────────────────────
SYSTEM_PROMPT = """あなたは音声認識テキストの校正と話者分離の専門家です。
以下の2つのタスクを同時に行ってください。

【タスク1: テキスト修正】
- 誤字脱字・同音異義語の誤認識を修正する
- 句読点が抜けている場合は適切に補う
- 文脈上おかしな表現を自然な日本語に修正する
- 固有名詞（社名・人名・製品名）は文脈から推測して修正する
- 意味が変わるような大きな書き換えは行わない

【タスク2: 話者判定】
会話の流れ・文体・内容から各セグメントの話者を推定し、ラベルを付ける。
- 会話が2人の場合: "A"（最初に話す人）と "B" を割り当てる
- 3人以上の場合: "A", "B", "C" ... と増やす
- 1人のみ、または判断できない場合: "unknown"
- 会話の文脈（質問と回答、話題の転換など）を手がかりにする
- タイムスタンプの間隔（話者交代のタイミング）も考慮する

【入力形式】
JSON配列: [{"id": 0, "start": 0.0, "end": 3.2, "text": "原文"}, ...]

【出力形式】
必ずJSON配列のみを出力:
[{"id": 0, "text": "修正済みテキスト", "speaker": "A"}, ...]
説明文・コードブロック・改行は含めない。"""


def _parse_raw(raw: str) -> list[dict]:
    """GPT レスポンスのコードブロックを除去して JSON パース"""
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def correct_and_detect_speakers(segments: list[Segment]) -> list[AnnotatedSegment]:
    """
    Whisper セグメントリストを受け取り、
    テキスト修正 + 話者ラベル付きで返す。

    Parameters
    ----------
    segments : transcriber.transcribe() の出力

    Returns
    -------
    annotated : [{"start": ..., "end": ..., "text": ..., "speaker": "A"/"B"/"unknown"}, ...]
    """
    client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION,
    )

    # GPT に渡すペイロード（タイムスタンプも含める＝話者判定の手がかりになる）
    payload = [
        {"id": i, "start": seg["start"], "end": seg["end"], "text": seg["text"]}
        for i, seg in enumerate(segments)
    ]

    chunk_size = 30
    results: dict[int, dict] = {}  # id → {text, speaker}

    for chunk_start in range(0, len(payload), chunk_size):
        chunk = payload[chunk_start : chunk_start + chunk_size]
        chunk_end = chunk_start + len(chunk)
        print(f"[Corrector] 修正 + 話者判定中... ({chunk_start+1}〜{chunk_end}/{len(payload)})")

        response = client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": json.dumps(chunk, ensure_ascii=False)},
            ],
            temperature=0.1,
            max_tokens=4096,
        )

        raw = response.choices[0].message.content
        try:
            items = _parse_raw(raw)
            for item in items:
                results[item["id"]] = {
                    "text":    item.get("text", ""),
                    "speaker": item.get("speaker", "unknown"),
                }
        except (json.JSONDecodeError, KeyError) as e:
            print(f"[Corrector] パースエラー: {e} — 元テキスト・unknown で代替")
            for item in chunk:
                results[item["id"]] = {"text": item["text"], "speaker": "unknown"}

    # タイムスタンプを維持しながらマージ
    annotated: list[AnnotatedSegment] = []
    speakers_found: set[str] = set()

    for i, seg in enumerate(segments):
        r = results.get(i, {"text": seg["text"], "speaker": "unknown"})
        speaker = r["speaker"]
        speakers_found.add(speaker)
        annotated.append({
            "start":   seg["start"],
            "end":     seg["end"],
            "text":    r["text"],
            "speaker": speaker,
        })

    # 結果サマリーを表示
    print(f"[Corrector] 完了: {len(annotated)} セグメント")
    _print_speaker_summary(annotated, speakers_found)

    return annotated


def _print_speaker_summary(segments: list[AnnotatedSegment], speakers: set[str]) -> None:
    """話者ごとの発言数をログ表示"""
    if speakers == {"unknown"}:
        print("[Corrector] 話者: 会話形式ではないと判定（全セグメント unknown）")
        return

    from collections import Counter
    counts = Counter(seg["speaker"] for seg in segments)
    print("[Corrector] 話者判定結果:")
    for spk in sorted(counts):
        # 代表的な発言を1件表示
        sample = next(s["text"] for s in segments if s["speaker"] == spk)
        print(f"  話者{spk}: {counts[spk]}発言  例)「{sample[:30]}{'...' if len(sample)>30 else ''}」")


# ── 旧インターフェース互換（pipeline.py からそのまま呼べる）────
def correct_segments(segments: list[Segment]) -> list[AnnotatedSegment]:
    """pipeline.py の既存呼び出しと互換性を維持するエイリアス"""
    return correct_and_detect_speakers(segments)
