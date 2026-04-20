"""
transcriber.py
構築済みの ローカルWhisperサーバー（server.py / port 8000）に
multipart/form-data でリクエストを送り、セグメントを取得する。

whisper ライブラリは不要（インポートしない）。
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import TypedDict

import requests

from config import WHISPER_SERVER_URL, WHISPER_LANGUAGE


# ── 型定義 ────────────────────────────────────────────────────
class Segment(TypedDict):
    start: float   # 秒
    end:   float   # 秒
    text:  str


# ── 内部ユーティリティ ────────────────────────────────────────

def _check_server() -> None:
    """Whisper サーバーの起動確認"""
    try:
        r = requests.get(f"{WHISPER_SERVER_URL}/health", timeout=5)
        r.raise_for_status()
        info = r.json()
        print(f"[Whisper Server] 接続OK — モデル: {info.get('model', '?')}")
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Whisper サーバーに接続できません: {WHISPER_SERVER_URL}\n"
            "whisper_server/server.py を先に起動してください。\n"
            "  cd whisper_server\n"
            "  python server.py"
        )
    except Exception as e:
        raise RuntimeError(f"Whisper サーバーのヘルスチェックに失敗: {e}")


# ── 公開 API ──────────────────────────────────────────────────

def transcribe(audio_path: str | Path) -> list[Segment]:
    """
    ローカル Whisper サーバーに音声ファイルを送信し、
    セグメントリストを返す。

    Parameters
    ----------
    audio_path : 音声ファイルパス (.wav / .mp3 / .m4a など)

    Returns
    -------
    segments : [{"start": 0.0, "end": 3.2, "text": "こんにちは"}, ...]
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"音声ファイルが見つかりません: {audio_path}")

    _check_server()

    print(f"[Whisper Server] 文字起こし開始: {audio_path.name}")

    with open(audio_path, "rb") as f:
        response = requests.post(
            f"{WHISPER_SERVER_URL}/transcribe",
            files={"file": (audio_path.name, f, _mime_type(audio_path))},
            data={"language": WHISPER_LANGUAGE},
            # 長い音声でも待つ（タイムアウト: 接続5秒, 読み取り30分）
            timeout=(5, 1800),
        )

    if not response.ok:
        raise RuntimeError(
            f"Whisper サーバーがエラーを返しました: "
            f"HTTP {response.status_code} — {response.text}"
        )

    body = response.json()

    if not body.get("success"):
        raise RuntimeError(f"文字起こし失敗: {body}")

    # サーバーの segments をそのまま使う（形式は同一）
    raw_segments = body.get("segments", [])
    segments: list[Segment] = [
        {
            "start": round(float(seg["start"]), 3),
            "end":   round(float(seg["end"]),   3),
            "text":  seg["text"].strip(),
        }
        for seg in raw_segments
        if seg.get("text", "").strip()   # 空セグメントを除外
    ]

    print(f"[Whisper Server] {len(segments)} セグメントを取得")
    if segments:
        total_sec = segments[-1]["end"]
        print(f"[Whisper Server] 音声長: {total_sec:.1f}秒")

    return segments


def save_segments(segments: list[Segment], output_path: str | Path) -> None:
    """セグメントリストをJSONファイルに保存"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False, indent=2)
    print(f"[Whisper Server] セグメントを保存: {output_path}")


# ── ヘルパー ──────────────────────────────────────────────────

def _mime_type(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".wav":  "audio/wav",
        ".mp3":  "audio/mpeg",
        ".m4a":  "audio/mp4",
        ".ogg":  "audio/ogg",
        ".flac": "audio/flac",
        ".webm": "audio/webm",
    }.get(ext, "application/octet-stream")
