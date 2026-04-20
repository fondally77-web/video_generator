"""
ローカルWhisper音声認識サーバー
FastAPIを使用してOpenAI Whisperをローカルで実行

【ffmpeg不要版】
WAVファイルは soundfile で numpy配列として読み込み、
Whisperに直接渡すことでffmpegへの依存をなくす。
mp3/m4aなど他形式を使う場合は別途ffmpegが必要。
"""

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import whisper
import numpy as np
import soundfile as sf
import io
import os
import uvicorn

app = FastAPI(title="Local Whisper Server")

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Whisperモデルをロード
MODEL_SIZE = os.environ.get("WHISPER_MODEL", "small")
print(f"[Whisper] モデルをロード中: {MODEL_SIZE}...")
model = whisper.load_model(MODEL_SIZE)
print(f"[Whisper] モデル準備完了: {MODEL_SIZE}")


def load_audio_as_array(content: bytes, filename: str) -> np.ndarray:
    """
    音声バイト列を Whisper が期待する numpy配列（float32, 16kHz mono）に変換する。
    soundfile を使うため ffmpeg 不要（WAV/FLAC/OGG 対応）。
    """
    # soundfile でバイト列から直接読み込む
    audio_buf = io.BytesIO(content)
    audio, sample_rate = sf.read(audio_buf, dtype="float32")

    # ステレオ→モノラル変換
    if audio.ndim == 2:
        audio = audio.mean(axis=1)

    # Whisper が要求するサンプルレート: 16000 Hz にリサンプリング
    if sample_rate != whisper.audio.SAMPLE_RATE:
        # scipy.signal.resample_poly で高品質リサンプリング
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(whisper.audio.SAMPLE_RATE, sample_rate)
        audio = resample_poly(audio, whisper.audio.SAMPLE_RATE // g, sample_rate // g)
        audio = audio.astype(np.float32)

    return audio


@app.get("/")
async def root():
    return {"status": "ok", "model": MODEL_SIZE}


@app.get("/health")
async def health():
    return {"status": "healthy", "model": MODEL_SIZE}


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...), language: str = "ja"):
    """
    音声ファイルを書き起こし（ffmpeg不要）
    """
    try:
        print(f"[Whisper] 音声認識開始: {file.filename}")
        content = await file.read()

        # numpy配列として読み込む（ffmpeg不使用）
        audio_array = load_audio_as_array(content, file.filename or "audio.wav")
        print(f"   音声読み込み完了: {len(audio_array)/whisper.audio.SAMPLE_RATE:.1f}秒")

        # Whisperで書き起こし（numpy配列を直接渡す）
        result = model.transcribe(
            audio_array,        # ← ファイルパスではなくnumpy配列
            language=language,
            verbose=False,
            word_timestamps=True,
        )

        # セグメント形式に変換
        segments = []
        for seg in result.get("segments", []):
            segments.append({
                "start": seg["start"],
                "end":   seg["end"],
                "text":  seg["text"].strip(),
            })

        print(f"[Whisper] 音声認識完了: {len(segments)}セグメント")

        return {
            "success":  True,
            "text":     result["text"],
            "segments": segments,
            "language": result.get("language", language),
        }

    except Exception as e:
        print(f"[Whisper] エラー: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    port = int(os.environ.get("WHISPER_PORT", 8000))
    print(f"[Whisper] Server starting on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)
