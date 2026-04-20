"""
segment_merger.py — v4

話題境界でのみ分割し、短すぎるセグメントを後処理で吸収する。
"""

from __future__ import annotations
import json
from openai import AzureOpenAI
from config import (
    AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_API_VERSION, AZURE_OPENAI_DEPLOYMENT,
)

SENTENCE_TERMINALS = set("。．！？!?")
SHORT_TEXT_LEN = 14
SHORT_DURATION_MS = 1500
SHORT_RUN_DURATION_MS = 2500
MAX_BOUNDARY_SHIFT = 2
TRANSITION_PREFIXES = (
    "あるいは", "または", "そして", "その", "それから", "さらに",
    "ただ", "つづいて", "つづき", "ちなみに", "ところで", "つまり", "なお",
)
CONTINUATION_SUFFIXES = {"、", "，", ","}

SEMANTIC_PREFIXES = (
    "では", "それでは", "その後", "次に", "続いて", "さらに", "一方で", "一方では", "逆に",
    "ところで", "さて", "ちなみに", "first", "next", "then", "finally", "summary",
)
EXAMPLE_KEYWORDS = (
    "例えば", "例として", "具体的には", "一例として", "事例として", "例を挙げると",
)
SUMMARY_KEYWORDS = (
    "まとめ", "結論", "要するに", "要約すると", "総括すると", "まとめると",
)
ENUMERATION_KEYWORDS = (
    "1つ目", "一つ目", "2つ目", "二つ目", "3つ目", "三つ目", "第一に", "第二に", "第三に",
    "まず", "次に", "最後に", "first", "second", "third", "finally",
)
VIEWPOINT_KEYWORDS = (
    "視点", "視座", "観点", "立場", "他方", "別の観点",
)
QUESTION_SUFFIXES = (
    "？", "?", "ですか？", "ますか？", "でしょうか？", "かな？", "かしら？", "でしょう？",
    "ですか", "ますか", "でしょうか", "かな", "かしら", "でしょう",
)

SYSTEM_PROMPT = """あなたは音声トランスクリプトの話題分析専門家です。
与えられたセグメントリストを読み、「話題が切り替わる」インデックスを返してください。

【判断基準】
- 新しい概念・用語の説明が始まる
- 話者が「次に〜」「では〜」「続いて〜」などの転換語を使う
- 質問から回答へ、または逆に切り替わる
- 前の話題のまとめ・結論が出た直後

【制約】
- 境界は「そのインデックスから新しい話題が始まる」番号で指定する
- 最初（0）は含めない
- 連続した番号は避ける（最低3セグメントはまとめる）
- 全体の話題数が多すぎる場合は大きな区切りのみ返す

【入力】JSON配列: [{"id": 0, "text": "...", "speaker": "A"}, ...]
【出力】JSON配列のみ: [5, 12, 23, ...]"""


def _detect_boundaries(segments: list[dict]) -> list[int]:
    client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION,
    )
    CHUNK = 60
    all_boundaries: list[int] = []
    for start in range(0, len(segments), CHUNK):
        chunk = segments[start:start + CHUNK]
        payload = [{"id": i + start, "text": s["text"][:60], "speaker": s.get("speaker", "?")}
                   for i, s in enumerate(chunk)]
        end = start + len(chunk)
        print(f"[Merger] 話題境界を検出中... ({start+1}〜{end}/{len(segments)})")
        resp = client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.1, max_tokens=512,
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        try:
            boundaries = json.loads(raw)
            if isinstance(boundaries, list):
                all_boundaries.extend([b for b in boundaries if isinstance(b, int)])
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[Merger] パースエラー: {e}")
    return sorted(set(all_boundaries))


def _strip_text(seg: dict) -> str:
    return (seg.get("text") or "").strip()


def _ends_with_sentence(seg: dict) -> bool:
    text = _strip_text(seg)
    return bool(text) and text[-1] in SENTENCE_TERMINALS


def _starts_with_transition(seg: dict) -> bool:
    text = _strip_text(seg)
    return bool(text) and any(text.startswith(pref) for pref in TRANSITION_PREFIXES)


def _ends_with_continuation(seg: dict) -> bool:
    text = _strip_text(seg)
    return bool(text) and text[-1] in CONTINUATION_SUFFIXES


def _is_weak_segment(seg: dict) -> bool:
    text = _strip_text(seg)
    if not text:
        return True
    duration = seg.get("duration_ms", 0)
    if len(text) < SHORT_TEXT_LEN and duration < SHORT_DURATION_MS:
        return True
    if _starts_with_transition(seg):
        return True
    return False


def _contains_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _matches_prefix(text: str, prefixes: tuple[str, ...]) -> bool:
    return any(text.startswith(prefix) for prefix in prefixes)


def _is_question_text(text: str) -> bool:
    return any(text.endswith(suffix) for suffix in QUESTION_SUFFIXES)


def _collect_semantic_boundaries(segments: list[dict]) -> list[int]:
    boundaries: set[int] = set()
    previous_was_question = False
    for idx, seg in enumerate(segments):
        text = _strip_text(seg)
        if not text:
            previous_was_question = False
            continue
        normalized = text.casefold()
        if idx > 0 and _matches_prefix(normalized, SEMANTIC_PREFIXES):
            boundaries.add(idx)
        if idx > 0 and _contains_keyword(normalized, EXAMPLE_KEYWORDS):
            boundaries.add(idx)
        if idx > 0 and _contains_keyword(normalized, SUMMARY_KEYWORDS):
            boundaries.add(idx)
        if idx > 0 and _contains_keyword(normalized, ENUMERATION_KEYWORDS):
            boundaries.add(idx)
        if idx > 0 and _contains_keyword(normalized, VIEWPOINT_KEYWORDS):
            boundaries.add(idx)
        is_question = _is_question_text(text)
        if idx > 0 and previous_was_question and not is_question:
            boundaries.add(idx)
        previous_was_question = is_question
    return sorted(boundaries)


def _total_duration(run: list[dict]) -> int:
    return sum(s.get("duration_ms", 0) for s in run)


def _should_drop_boundary(segments: list[dict], boundary: int, next_boundary: int) -> bool:
    if boundary <= 0 or boundary >= len(segments):
        return False
    limit = min(boundary + 2, next_boundary)
    run = segments[boundary:limit]
    if not run:
        return False
    if any(not _is_weak_segment(seg) for seg in run):
        return False
    if _total_duration(run) >= SHORT_RUN_DURATION_MS:
        return False
    return True


def _compute_shift(segments: list[dict], boundary: int, next_boundary: int) -> int:
    if not _is_weak_segment(segments[boundary]):
        return 0
    max_shift = min(MAX_BOUNDARY_SHIFT, next_boundary - boundary - 1)
    if max_shift <= 0:
        return 0
    for lookahead in range(1, max_shift + 1):
        idx = boundary + lookahead
        if idx >= next_boundary:
            break
        if not _is_weak_segment(segments[idx]):
            return lookahead
    return 0


def _adjust_boundary_candidates(segments: list[dict], boundaries: list[int]) -> list[int]:
    if not segments:
        return []
    normalized = sorted({b for b in boundaries if isinstance(b, int)})
    normalized = [b for b in normalized if 0 < b < len(segments)]
    adjusted: list[int] = []
    for idx, boundary in enumerate(normalized):
        if adjusted and boundary <= adjusted[-1]:
            continue
        next_boundary = normalized[idx + 1] if idx + 1 < len(normalized) else len(segments)
        shift = _compute_shift(segments, boundary, next_boundary)
        if shift == 0 and _should_drop_boundary(segments, boundary, next_boundary):
            continue
        new_boundary = boundary + shift
        if new_boundary >= next_boundary:
            new_boundary = next_boundary - 1
        if new_boundary <= 0 or new_boundary >= len(segments):
            continue
        if adjusted and new_boundary <= adjusted[-1]:
            continue
        adjusted.append(new_boundary)
    return adjusted


def _build_speaker_runs(group: list[dict]) -> list[dict]:
    if not group:
        return []
    runs = []
    cur_spk = group[0].get("speaker", "unknown")
    cur_segs = [group[0]]
    for seg in group[1:]:
        spk = seg.get("speaker", "unknown")
        if spk == cur_spk:
            cur_segs.append(seg)
        else:
            runs.append({
                "speaker": cur_spk,
                "text": "".join(s["text"].strip() for s in cur_segs),
                "duration_ms": sum(s.get("duration_ms", 0) for s in cur_segs),
            })
            cur_spk = spk
            cur_segs = [seg]
    runs.append({
        "speaker": cur_spk,
        "text": "".join(s["text"].strip() for s in cur_segs),
        "duration_ms": sum(s.get("duration_ms", 0) for s in cur_segs),
    })
    return runs


def _find_strong_start_offset(group: list[dict], start_idx: int, max_shift: int = MAX_BOUNDARY_SHIFT) -> int:
    for offset in range(1, max_shift + 1):
        target_idx = start_idx + offset
        if target_idx >= len(group):
            break
        if not _is_weak_segment(group[target_idx]):
            return offset
    return 0


def merge_segments(
    segments: list[dict],
    target_ms: int = 25_000,
    max_ms: int    = 40_000,
    min_ms: int    = 5_000,
) -> list[dict]:
    if not segments:
        return []

    # `target_ms`/`max_ms` are retained for compatibility only.
    _ = (target_ms, max_ms)

    for idx, seg in enumerate(segments):
        seg.setdefault("index", idx)
        if "duration_ms" not in seg:
            s, e = seg.get("start"), seg.get("end")
            seg["duration_ms"] = max(1, int(round((e - s) * 1000))) if s is not None and e is not None else 0

    speakers = {s.get("speaker", "unknown") for s in segments}
    is_dialogue = len(speakers - {"unknown"}) >= 2
    print(f"[Merger] {'会話' if is_dialogue else 'モノローグ'}形式（話者: {sorted(speakers)}）")

    # ── 話題境界でグループ化 ─────────────────────────────
    boundaries = _detect_boundaries(segments)
    boundaries.extend(_collect_semantic_boundaries(segments))
    boundaries = _adjust_boundary_candidates(segments, boundaries)
    boundary_set = {0} | set(boundaries) | {len(segments)}
    sorted_bounds = sorted(boundary_set)
    print(f"[Merger] 話題境界: {sorted(set(boundaries))} ({len(sorted_bounds)-1}グループ)")

    topic_groups: list[list[dict]] = []
    for i in range(len(sorted_bounds) - 1):
        g = segments[sorted_bounds[i]: sorted_bounds[i + 1]]
        if g:
            topic_groups.append(g)

    # ── グループは GPT のトピック境界まま使用 ────────────────
    split_groups: list[list[dict]] = topic_groups[:]

    # ── 各グループを1セグメントに統合 ────────────────────
    merged: list[dict] = []
    for group in split_groups:
        base = dict(group[0])
        base["text"] = "".join(s["text"].strip() for s in group)
        base["duration_ms"] = sum(s.get("duration_ms", 0) for s in group)
        base["merged_indices"] = [s["index"] for s in group]
        base["merged_count"] = len(group)
        base["speaker_runs"] = _build_speaker_runs(group)
        if base["speaker_runs"]:
            base["speaker"] = max(base["speaker_runs"], key=lambda r: r["duration_ms"])["speaker"]
        for key in ("slide_layout", "slide_title", "slide_sub",
                    "slide_items", "slide_icon", "slide_number"):
            base.pop(key, None)
        merged.append(base)

    # ── 短すぎるセグメントを前に吸収 ─────────────────────
    if len(merged) > 1:
        cleaned = [merged[0]]
        for seg in merged[1:]:
            if seg["duration_ms"] < min_ms:
                # 前のセグメントに吸収
                prev = cleaned[-1]
                prev["text"] += seg["text"]
                prev["duration_ms"] += seg["duration_ms"]
                prev["merged_indices"].extend(seg.get("merged_indices", []))
                prev["merged_count"] = len(prev["merged_indices"])
                prev["speaker_runs"].extend(seg.get("speaker_runs", []))
                print(f"[Merger] 吸収: \"{seg['text'][:20]}...\" ({seg['duration_ms']/1000:.1f}秒) → 前のスライドに統合")
            else:
                cleaned.append(seg)
        merged = cleaned

    # ── start_ms を再計算 ─────────────────────────────────
    GAP = 300
    cur = 0
    for s in merged:
        s["start_ms"] = cur
        cur += s["duration_ms"] + GAP

    # ── サマリー ──────────────────────────────────────────
    durs = [s["duration_ms"] / 1000 for s in merged]
    avg = sum(durs) / max(len(durs), 1)
    spk_counts = {}
    for s in merged:
        spk = s.get("speaker", "?")
        spk_counts[spk] = spk_counts.get(spk, 0) + 1

    print(f"[Merger] 完了: {len(segments)} セグ → {len(merged)} スライド "
          f"（平均 {avg:.1f}秒, 最短 {min(durs):.1f}秒, 最長 {max(durs):.1f}秒）")
    if is_dialogue:
        total_runs = sum(len(s.get("speaker_runs", [])) for s in merged)
        print(f"[Merger] 話者分布: {dict(sorted(spk_counts.items()))} / 話者ラン: {total_runs}")

    return merged
