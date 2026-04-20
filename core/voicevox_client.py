"""
voicevox_client.py
VOICEVOX エンジン（ローカル HTTP API）を使って
テキストから音声ファイルを生成する。

話者ラベル（A / B / unknown）に応じて VOICEVOX のスピーカーIDを切り替える。
"""
from __future__ import annotations
import json
import time
import wave
from pathlib import Path
from typing import TypedDict

import requests

from config import (
    VOICEVOX_BASE_URL, VOICEVOX_SPEAKERS, VOICEVOX_SPEAKER_ID,
    VOICEVOX_SPEED, VOICEVOX_INTONATION, VOICEVOX_PITCH, VOICEVOX_VOLUME,
    VOICEVOX_SPEAKER_PARAMS,
)
from core.corrector import AnnotatedSegment


# ── 型定義 ────────────────────────────────────────────
class AudioSegment(TypedDict):
    index:       int
    start_ms:    int
    duration_ms: int
    text:        str
    speaker:     str   # "A" / "B" / "unknown"
    audio_file:  str
    run_durations_ms: list[int]


# ── 内部ユーティリティ ────────────────────────────────

def _check_voicevox() -> None:
    try:
        r = requests.get(f"{VOICEVOX_BASE_URL}/version", timeout=3)
        r.raise_for_status()
        print(f"[VOICEVOX] エンジン v{r.json()} に接続しました")
    except Exception as e:
        raise RuntimeError(
            "VOICEVOX エンジンに接続できません。\n"
            "VOICEVOX を起動してから再実行してください。\n"
            f"詳細: {e}"
        )


def _resolve_speaker_id(speaker_label: str) -> int:
    """
    話者ラベル（"A" / "B" / "unknown"）から
    VOICEVOX のスピーカーIDを解決する。
    config.VOICEVOX_SPEAKERS に定義がなければ VOICEVOX_SPEAKER_ID を使用。
    """
    return VOICEVOX_SPEAKERS.get(speaker_label, VOICEVOX_SPEAKER_ID)


def _synthesize(text: str, speaker_id: int, output_path: Path,
                speaker_label: str = "unknown") -> float:
    """
    テキスト + スピーカーID → 音声合成 → wav 保存 → 秒数を返す
    speaker_label に基づいて話速・抑揚・ピッチを調整する。
    """
    # Step1: audio_query
    r1 = requests.post(
        f"{VOICEVOX_BASE_URL}/audio_query",
        params={"text": text, "speaker": speaker_id},
        timeout=30,
    )
    r1.raise_for_status()
    query = r1.json()

    # Step1.5: 音声パラメータを上書き
    # まずグローバルデフォルトを適用
    query["speedScale"]      = VOICEVOX_SPEED
    query["intonationScale"] = VOICEVOX_INTONATION
    query["pitchScale"]      = VOICEVOX_PITCH
    query["volumeScale"]     = VOICEVOX_VOLUME
    # 話者ごとの個別設定があれば上書き
    speaker_params = VOICEVOX_SPEAKER_PARAMS.get(speaker_label, {})
    for key, val in speaker_params.items():
        query[key] = val

    # Step2: synthesis
    r2 = requests.post(
        f"{VOICEVOX_BASE_URL}/synthesis",
        params={"speaker": speaker_id},
        json=query,
        timeout=60,
    )
    r2.raise_for_status()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(r2.content)

    # 音声長を WAV ヘッダーから取得
    try:
        with wave.open(str(output_path), "rb") as wf:
            return wf.getnframes() / wf.getframerate()
    except Exception:
        return max(1.0, len(text) * 0.12)


# ── 公開 API ──────────────────────────────────────────

def generate_audio_segments(
    segments: list[AnnotatedSegment],
    output_dir: str | Path,
    gap_ms: int = 300,
) -> list[AudioSegment]:
    """
    話者ラベル付きセグメントから VOICEVOX 音声を生成する。
    話者ごとに異なるスピーカーIDを使用。

    Parameters
    ----------
    segments   : corrector.correct_segments() の出力（speaker フィールド付き）
    output_dir : wav ファイルの保存先
    gap_ms     : セグメント間の空白（ms）

    Returns
    -------
    audio_segments : Remotion に渡せる形式
    """
    _check_voicevox()

    # 使用するスピーカーIDを事前表示
    speakers_in_use = set()
    for seg in segments:
        for run in seg.get("speaker_runs", []):
            speakers_in_use.add(run.get("speaker", "unknown"))
        speakers_in_use.add(seg.get("speaker", "unknown"))
    print("[VOICEVOX] 話者 → スピーカーID マッピング:")
    for spk in sorted(speakers_in_use):
        sid = _resolve_speaker_id(spk)
        params = VOICEVOX_SPEAKER_PARAMS.get(spk, {})
        spd = params.get("speedScale", VOICEVOX_SPEED)
        into = params.get("intonationScale", VOICEVOX_INTONATION)
        print(f"  話者{spk} → ID {sid}  速度={spd}x  抑揚={into}x")

    output_dir = Path(output_dir)
    audio_dir  = output_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    audio_segments: list[AudioSegment] = []
    current_ms = 0

    for i, seg in enumerate(segments):
        runs = seg.get("speaker_runs", [])

        # speaker_runs がない場合は従来通り1音声
        if not runs:
            runs = [{"speaker": seg.get("speaker", "unknown"), "text": seg["text"].strip()}]

        audio_files = []
        total_duration_ms = 0
        first_audio = ""
        run_durations_ms: list[int] = []

        for j, run in enumerate(runs):
            text = run["text"].strip()
            if not text:
                continue

            speaker = run.get("speaker", "unknown")
            speaker_id = _resolve_speaker_id(speaker)
            audio_path = audio_dir / f"seg_{i:04d}_{j:02d}_{speaker}.wav"

            if j == 0:
                preview = text[:28] + ("..." if len(text) > 28 else "")
                label = f"[{len(runs)}ラン]" if len(runs) > 1 else f"[話者{speaker}]"
                print(f"[VOICEVOX] [{i+1}/{len(segments)}] {label} 「{preview}」")

            try:
                duration_sec = _synthesize(text, speaker_id, audio_path, speaker)
            except Exception as e:
                print(f"[VOICEVOX] エラー（スキップ）: {e}")
                continue

            dur_ms = int(duration_sec * 1000)
            total_duration_ms += dur_ms
            audio_files.append(str(audio_path.resolve()))
            run_durations_ms.append(dur_ms)
            if not first_audio:
                first_audio = str(audio_path.resolve())

        if not audio_files:
            continue

        entry: dict = {
            "index":       i,
            "start_ms":    current_ms,
            "duration_ms": total_duration_ms,
            "text":        seg["text"].strip(),
            "speaker":     seg.get("speaker", "unknown"),
            "audio_file":      first_audio,
            "audio_files":     audio_files,
            "run_durations_ms": run_durations_ms,
        }
        for key in ("slide_layout", "slide_title", "slide_sub",
                    "slide_items", "slide_icon", "slide_number"):
            if key in seg:
                entry[key] = seg[key]

        audio_segments.append(entry)

        current_ms += total_duration_ms + gap_ms
        time.sleep(0.05)

    total_sec = current_ms / 1000
    total_files = sum(len(s.get("audio_files", [])) for s in audio_segments)
    print(f"[VOICEVOX] 完了: {len(audio_segments)} スライド / {total_files} 音声ファイル / 総尺 {total_sec:.1f}秒")

    json_path = output_dir / "audio_segments.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(audio_segments, f, ensure_ascii=False, indent=2)
    print(f"[VOICEVOX] タイミング情報を保存: {json_path}")

    return audio_segments
