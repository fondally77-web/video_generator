"""
audio_to_video 設定ファイル
"""

import os

# ── Azure OpenAI ──────────────────────────────────────
AZURE_OPENAI_ENDPOINT = os.getenv(
    "AZURE_OPENAI_ENDPOINT", "https://<your-resource>.openai.azure.com/"
)
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "<your-api-key>")
AZURE_OPENAI_API_VERSION = "2024-02-15-preview"
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")

# ── Whisper サーバー（ローカル HTTP API）────────────────
# whisper_server/server.py を起動済みの状態で使用する
WHISPER_SERVER_URL = os.getenv("WHISPER_SERVER_URL", "http://127.0.0.1:8000")
WHISPER_LANGUAGE = "ja"

# ── VOICEVOX ─────────────────────────────────────────
VOICEVOX_BASE_URL = "http://127.0.0.1:50021"

# 話者ラベル → VOICEVOX スピーカーID のマッピング
# 利用可能なIDは VOICEVOX 起動後に http://127.0.0.1:50021/speakers で確認
# 主なID例:
#   3  = ずんだもん (ノーマル)     1  = ずんだもん (あまあま)
#   2  = 四国めたん (ノーマル)     0  = 四国めたん (あまあま)
#   8  = 春日部つむぎ              10 = 波音リツ
#   13 = 青山龍星 (男性)           14 = 冥鳴ひまり
VOICEVOX_SPEAKERS: dict[str, int] = {
    "A": 3,  # 話者A: ずんだもん(ノーマル)
    "B": 2,  # 話者B: 四国めたん(ノーマル)
    "unknown": 3,  # 判定不能時のフォールバック
}

# 会話でない場合（話者1人）のデフォルト
VOICEVOX_SPEAKER_ID = 3

# ── VOICEVOX 音声パラメータ ──────────────────────────
# audio_query の結果に上書きする値（None の場合はデフォルトを使用）
# speedScale    : 話速（1.0=標準, 1.15=やや速め, 1.3=速い）
# intonationScale: 抑揚（1.0=標準, 1.3=やや大きめ, 1.6=大きい）
# pitchScale    : ピッチ（0.0=標準, +で高く -で低く）
# volumeScale   : 音量（1.0=標準）
VOICEVOX_SPEED = 1.15  # 元音声18分 → 動画21分を埋める
VOICEVOX_INTONATION = 1.4  # 抑揚を強めて聞き取りやすく
VOICEVOX_PITCH = 0.0  # ピッチはそのまま
VOICEVOX_VOLUME = 1.0  # 音量はそのまま

# 話者ごとの個別設定（設定がない話者は上記のデフォルトを使用）
# キャラの個性を出す: B（四国めたん）はやや遅め＆抑揚大きめ
VOICEVOX_SPEAKER_PARAMS: dict[str, dict] = {
    "A": {
        "speedScale": 1.15,
        "intonationScale": 1.3,
    },  # ずんだもん: テンポよく
    "B": {
        "speedScale": 1.10,
        "intonationScale": 1.5,
        "pitchScale": 0.02,
    },  # 四国めたん: 抑揚豊かに
}

# ── 動画設定 ──────────────────────────────────────────
VIDEO_FPS = 30
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
BACKGROUND_COLOR = "#FFFFFF"
FONT_COLOR = "#000000"
HIGHLIGHT_COLOR = "#000000"
FONT_SIZE = 64

# ── 出力先 ────────────────────────────────────────────
OUTPUT_DIR = "output"

# ── UIオーバーライド ──────────────────────────────────
# slide_editor.py の設定パネルから保存された値を自動で上書きする。
# config_overrides.json が存在しなければ何もしない。
import json as _json
from pathlib import Path as _Path

_OVERRIDES_PATH = _Path(__file__).parent / "config_overrides.json"


def _load_overrides() -> None:
    if not _OVERRIDES_PATH.exists():
        return
    try:
        data = _json.loads(_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    g = globals()
    # 単純な値（int / float / str）
    _SIMPLE = {
        "VOICEVOX_SPEAKER_ID",
        "VOICEVOX_SPEED",
        "VOICEVOX_INTONATION",
        "VOICEVOX_PITCH",
        "VOICEVOX_VOLUME",
        "VIDEO_FPS",
        "VIDEO_WIDTH",
        "VIDEO_HEIGHT",
        "BACKGROUND_COLOR",
        "FONT_COLOR",
        "HIGHLIGHT_COLOR",
        "FONT_SIZE",
    }
    for key in _SIMPLE:
        if key in data:
            g[key] = data[key]
    # dict 型
    if "VOICEVOX_SPEAKERS" in data and isinstance(
        data["VOICEVOX_SPEAKERS"], dict
    ):
        g["VOICEVOX_SPEAKERS"] = data["VOICEVOX_SPEAKERS"]
    if "VOICEVOX_SPEAKER_PARAMS" in data and isinstance(
        data["VOICEVOX_SPEAKER_PARAMS"], dict
    ):
        g["VOICEVOX_SPEAKER_PARAMS"] = data["VOICEVOX_SPEAKER_PARAMS"]


_load_overrides()
