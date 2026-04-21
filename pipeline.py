"""
pipeline.py — v4（パイプライン順序修正版）

処理フロー（重要: マージ後にスライド生成）:
  STEP 1: Whisper 文字起こし
  STEP 2: Azure OpenAI テキスト修正 + 話者判定
  STEP 3: AI話題分割 + 話者交代マージ
  STEP 4: Azure OpenAI スライド構造生成（マージ済みテキストから）
  STEP 5: VOICEVOX 音声合成
  STEP 6: Remotion レンダリング

再開オプション:
  --resume-raw      segments_raw.json から再開（テキスト修正〜）
  --resume          segments_corrected.json から再開（マージ〜）
  --resume-merged   segments_merged.json から再開（スライド生成〜）
  --resume-slides   segments_with_slides.json から再開（VOICEVOX〜）
  --resume-audio    audio_segments.json から再開（Remotionのみ）

テキスト入力オプション:
  --text                テキストファイルから開始（Whisperスキップ）
  --skip-correction     テキスト入力時にSTEP2（Azure OpenAI修正・話者判定）をスキップ
"""

from __future__ import annotations
import argparse, json, sys
from pathlib import Path

# Windows の cp932 コンソールでも絵文字を出せるように stdout/stderr を UTF-8 化
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from config import OUTPUT_DIR
from core.transcriber import transcribe, save_segments
from core.corrector import correct_segments
from core.slide_builder import build_slides, save_slides
from core.segment_merger import merge_segments
from core.voicevox_client import generate_audio_segments
from core.video_builder import render_video


def run(
    audio,
    output=None,
    skip_voicevox=False,
    skip_correction=False,
    resume_raw=None,
    resume=None,
    resume_merged=None,
    resume_slides=None,
    resume_audio=None,
    target_sec=25,
    text=None,
    preview=False,
):

    out = Path(OUTPUT_DIR)
    out.mkdir(exist_ok=True)
    print("=" * 60)
    print("🎬  Audio → Video Pipeline v4")
    print(f"    目安尺: {target_sec}秒/スライド")
    print("=" * 60)

    # ── Remotionのみ ──────────────────────────────────────
    if resume_audio:
        print(f"\n[SKIP] Remotionのみ実行: {resume_audio}")
        segs = json.load(open(resume_audio, encoding="utf-8"))
        _render(segs, audio, output, preview=preview)
        return

    # ── スライド生成済みからVOICEVOX再開 ──────────────────
    if resume_slides:
        print(f"\n[SKIP] VOICEVOXから再開: {resume_slides}")
        slides = json.load(open(resume_slides, encoding="utf-8"))
        print(f"       {len(slides)} スライド読み込み完了")
        if not skip_voicevox:
            _voicevox_and_render(slides, audio, output, out, preview=preview)
        return

    # ── マージ済みからスライド生成再開 ────────────────────
    if resume_merged:
        print(f"\n[SKIP] スライド生成から再開: {resume_merged}")
        merged = json.load(open(resume_merged, encoding="utf-8"))
        print(f"       {len(merged)} スライド読み込み完了")
        slides = _build_and_save_slides(merged, out)
        if not skip_voicevox:
            _voicevox_and_render(slides, audio, output, out, preview=preview)
        return

    # ── テキストファイルから開始（Whisperスキップ）────────
    if text:
        print(f"\n📄 テキストファイルから開始: {text}")
        segments = _text_to_segments(text)
        print(f"   {len(segments)} セグメントに分割")
        if skip_correction:
            print("\n[SKIP] --skip-correction（STEP 2スキップ）")
            corrected = segments
        else:
            print("\n✏️  STEP 2/6: Azure OpenAI テキスト修正 + 話者判定...")
            corrected = correct_segments(segments)
            save_segments(corrected, out / "segments_corrected.json")

    # ── Whisper生データからテキスト修正再開 ──────────────
    elif resume_raw:
        print(f"\n[SKIP] テキスト修正から再開: {resume_raw}")
        raw = json.load(open(resume_raw, encoding="utf-8"))
        print("\n✏️  STEP 2/6: Azure OpenAI テキスト修正 + 話者判定...")
        corrected = correct_segments(raw)
        save_segments(corrected, out / "segments_corrected.json")

    # ── 修正済みからマージ再開 ────────────────────────────
    elif resume:
        print(f"\n[SKIP] マージから再開: {resume}")
        corrected = json.load(open(resume, encoding="utf-8"))
        for s in corrected:
            s.setdefault("speaker", "unknown")

    else:
        # ── STEP 1: Whisper ───────────────────────────────
        print("\n📝 STEP 1/6: Whisper 文字起こし...")
        raw = transcribe(audio)
        save_segments(raw, out / "segments_raw.json")

        # ── STEP 2: テキスト修正 + 話者判定 ───────────────
        print("\n✏️  STEP 2/6: Azure OpenAI テキスト修正 + 話者判定...")
        corrected = correct_segments(raw)
        save_segments(corrected, out / "segments_corrected.json")

    # ── STEP 3: マージ（話題境界 + 話者交代）─────────────
    print(
        f"\n🔀 STEP 3/6: AI話題分割マージ（目安 {target_sec}秒/スライド）..."
    )
    merged = merge_segments(corrected, target_ms=target_sec * 1000)
    save_slides(merged, out / "segments_merged.json")

    # ── STEP 4: スライド構造生成（マージ済みから）────────
    slides = _build_and_save_slides(merged, out)

    if skip_voicevox:
        print("\n[SKIP] --skip-voicevox")
        return

    # ── STEP 5+6: VOICEVOX + Remotion ─────────────────────
    _voicevox_and_render(slides, audio, output, out, preview=preview)


def _text_to_segments(text_path: str) -> list[dict]:
    """
    テキストファイル → Whisper出力と同形式のセグメントリストに変換。
    空行区切りで段落分割。タイムスタンプは 0 で初期化。
    """
    text = Path(text_path).read_text(encoding="utf-8")
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return [
        {"text": p, "start": 0.0, "end": 0.0, "speaker": "A"}
        for p in paragraphs
    ]


def _build_and_save_slides(merged, out):
    """マージ済みセグメントからスライド構造を生成"""
    print("\n🎨 STEP 4/6: Azure OpenAI スライド構造生成...")
    slides = build_slides(merged)
    save_slides(slides, out / "segments_with_slides.json")
    return slides


def _voicevox_and_render(slides, audio, output, out, preview=False):
    print("\n🔊 STEP 5/6: VOICEVOX 音声合成（話者別ボイス）...")
    audio_segs = generate_audio_segments(slides, out)
    _render(audio_segs, audio, output, preview=preview)


def _render(segs, audio, output, preview=False):
    print("\n🎬 STEP 6/6: Remotion レンダリング...")
    out_video = render_video(segs, audio, output, preview=preview)
    print(f"\n✅ 完了: {out_video}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--audio", default=None, help="音声ファイルから開始")
    p.add_argument(
        "--text",
        default=None,
        help="テキストファイルから開始（Whisperスキップ）",
    )
    p.add_argument("--output", default=None)
    p.add_argument("--target-sec", type=int, default=25)
    p.add_argument("--skip-voicevox", action="store_true")
    p.add_argument(
        "--skip-correction",
        action="store_true",
        help="テキスト入力時にSTEP2（修正・話者判定）をスキップ",
    )
    p.add_argument(
        "--resume-raw",
        default=None,
        help="segments_raw.json から再開（テキスト修正〜）",
    )
    p.add_argument(
        "--resume", default=None, help="segments_corrected.json から再開"
    )
    p.add_argument(
        "--resume-merged",
        default=None,
        help="segments_merged.json から再開（スライド生成〜）",
    )
    p.add_argument(
        "--resume-slides",
        default=None,
        help="segments_with_slides.json から再開（VOICEVOX〜）",
    )
    p.add_argument(
        "--resume-audio",
        default=None,
        help="audio_segments.json から再開（Remotionのみ）",
    )
    p.add_argument(
        "--preview",
        action="store_true",
        help="低解像度(960x540)でレンダリング。確認用に高速化",
    )
    a = p.parse_args()

    # audio / text どちらかは必須（resume系は除く）
    if (
        not a.audio
        and not a.text
        and not any(
            [
                a.resume_raw,
                a.resume,
                a.resume_merged,
                a.resume_slides,
                a.resume_audio,
            ]
        )
    ):
        p.error("--audio または --text を指定してください")

    run(
        a.audio,
        a.output,
        a.skip_voicevox,
        a.skip_correction,
        a.resume_raw,
        a.resume,
        a.resume_merged,
        a.resume_slides,
        a.resume_audio,
        a.target_sec,
        a.text,
        a.preview,
    )
