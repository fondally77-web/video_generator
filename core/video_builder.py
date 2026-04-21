"""
video_builder.py
Remotion プロジェクト用の props.json を生成し動画を書き出す。

音声ファイルは一時HTTPサーバー経由で提供する
（Remotion は file:// を読めないため）
"""
from __future__ import annotations
import json
import os
import platform
import subprocess
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

from config import (
    VIDEO_FPS, VIDEO_WIDTH, VIDEO_HEIGHT,
    BACKGROUND_COLOR, FONT_COLOR, HIGHLIGHT_COLOR, FONT_SIZE,
    OUTPUT_DIR,
)
from core.voicevox_client import AudioSegment

import cv2
import numpy as np

def get_total_frames(video_path: str | Path = None) -> int:
    """指定動画ファイルの総フレーム数を返す。未指定時はOUTPUT_DIR/result.mp4を参照。"""
    vp = Path(video_path) if video_path else Path(OUTPUT_DIR) / "result.mp4"
    cap = cv2.VideoCapture(str(vp))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return total

def get_frame(index: int, video_path: str | Path = None) -> np.ndarray:
    """指定インデックスのフレームをRGB配列で返す。"""
    vp = Path(video_path) if video_path else Path(OUTPUT_DIR) / "result.mp4"
    cap = cv2.VideoCapture(str(vp))
    cap.set(cv2.CAP_PROP_POS_FRAMES, index)
    ret, frame = cap.read()
    cap.release()
    if not ret:
        raise IndexError(f"Frame {index} is out of range for {vp}")
    # BGR→RGB
    return frame[:, :, ::-1]


REMOTION_PROJECT_DIR = Path(__file__).parent.parent / "remotion_project"
IS_WINDOWS = platform.system() == "Windows"

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    os.path.expandvars(r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe"),
]


def _find_chrome() -> str | None:
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    return None


def _run(cmd_list: list[str], cwd: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, **(extra_env or {})}
    if IS_WINDOWS:
        cmd_str = " ".join(f'"{c}"' if " " in c else c for c in cmd_list)
        return subprocess.run(cmd_str, cwd=str(cwd), shell=True, env=env)
    else:
        return subprocess.run(cmd_list, cwd=str(cwd), env=env)


def _start_file_server(serve_dir: Path, port: int = 18080) -> HTTPServer:
    """
    serve_dir を http://localhost:PORT/ で配信する一時HTTPサーバーを起動する。
    ThreadingHTTPServer で並列リクエストに対応。
    """
    class QuietHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(serve_dir), **kwargs)
        def log_message(self, format, *args):
            pass

    # ThreadingHTTPServer: リクエストごとに別スレッドで処理
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", port), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def build_props(
    audio_segments: list[AudioSegment],
    original_audio: str | Path,
    audio_base_url: str,
    output_dir: Path,
) -> dict:
    """
    audio_file のローカルパスを http://localhost:PORT/... に変換してpropsを構築する。
    """
    total_ms = 0
    if audio_segments:
        last = audio_segments[-1]
        total_ms = last["start_ms"] + last["duration_ms"] + 500
    total_frames = int((total_ms / 1000) * VIDEO_FPS)

    # audio_file / audio_files パスを URL に変換
    converted_segments = []
    for seg in audio_segments:
        s = dict(seg)

        # audio_file（単数）を変換
        audio_path = Path(s.get("audio_file", ""))
        try:
            rel = audio_path.relative_to(output_dir)
            s["audio_file"] = f"{audio_base_url}/{rel.as_posix()}"
        except ValueError:
            s["audio_file"] = f"{audio_base_url}/audio/{audio_path.name}"

        # audio_files（複数 — 話者ランごとの音声）を変換
        if "audio_files" in s and isinstance(s["audio_files"], list):
            converted_files = []
            for af in s["audio_files"]:
                af_path = Path(af)
                try:
                    rel = af_path.relative_to(output_dir)
                    converted_files.append(f"{audio_base_url}/{rel.as_posix()}")
                except ValueError:
                    converted_files.append(f"{audio_base_url}/audio/{af_path.name}")
            s["audio_files"] = converted_files

        converted_segments.append(s)

    props = {
        "fps":              VIDEO_FPS,
        "durationInFrames": max(total_frames, VIDEO_FPS * 3),
        "width":            VIDEO_WIDTH,
        "height":           VIDEO_HEIGHT,
        "backgroundColor":  BACKGROUND_COLOR,
        "fontColor":        FONT_COLOR,
        "highlightColor":   HIGHLIGHT_COLOR,
        "fontSize":         FONT_SIZE,
        "originalAudio":    "",   # 元音声は使わない（volume=0のため省略）
        "segments":         converted_segments,
    }

    # config_overrides.json から見た目の設定を props に反映
    overrides_path = Path("config_overrides.json")
    if overrides_path.exists():
        try:
            overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
            # スケール
            if "TITLE_SCALE" in overrides:
                props["titleScale"] = float(overrides["TITLE_SCALE"])
            props["layoutSubScales"]     = overrides.get("LAYOUT_SUB_SCALES",     {})
            props["layoutItemScales"]    = overrides.get("LAYOUT_ITEM_SCALES",    {})
            props["layoutPaddingScales"] = overrides.get("LAYOUT_PADDING_SCALES", {})
            # ブランド表示
            props["showTitleBrand"] = bool(overrides.get("SHOW_TITLE_BRAND", False))
            props["brandText"]      = overrides.get("BRAND_TEXT", "NotebookLM")
            # 文字色
            if "TEXT_COLOR_PRIMARY" in overrides:
                props["textColorPrimary"] = overrides["TEXT_COLOR_PRIMARY"]
            if "TEXT_COLOR_SUB" in overrides:
                props["textColorSub"] = overrides["TEXT_COLOR_SUB"]
            if "TEXT_COLOR_ACCENT" in overrides:
                props["textColorAccent"] = overrides["TEXT_COLOR_ACCENT"]
            if "BACKGROUND_COLOR" in overrides:
                props["backgroundColor"] = overrides["BACKGROUND_COLOR"]
        except Exception:
            pass

    return props


def render_video(
    audio_segments: list[AudioSegment],
    original_audio: str | Path,
    output_path: str | Path | None = None,
    preview: bool = False,
) -> Path:
    """
    Remotion を呼んで動画をレンダリングする。

    preview=True のとき、低解像度・高圧縮のプレビュー動画を書き出す
    （デフォルト出力先は OUTPUT_DIR/result_preview.mp4）。
    """
    if output_path is None:
        fname = "result_preview.mp4" if preview else "result.mp4"
        out = Path(OUTPUT_DIR) / fname
    else:
        out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if not REMOTION_PROJECT_DIR.exists():
        raise FileNotFoundError(
            f"remotion_project フォルダが見つかりません: {REMOTION_PROJECT_DIR}"
        )

    # ── 音声ファイル配信用 HTTPサーバーを起動 ──────────────
    output_dir = Path(OUTPUT_DIR).resolve()
    http_port  = 18080
    print(f"[Remotion] 音声ファイル配信サーバーを起動: http://127.0.0.1:{http_port}/")
    file_server = _start_file_server(output_dir, http_port)
    time.sleep(0.5)  # サーバー起動待ち
    audio_base_url = f"http://127.0.0.1:{http_port}"

    try:
        # props を URL ベースで構築・保存
        props = build_props(audio_segments, original_audio, audio_base_url, output_dir)
        props_path = Path(OUTPUT_DIR) / "props.json"
        with open(props_path, "w", encoding="utf-8") as f:
            json.dump(props, f, ensure_ascii=False, indent=2)
        print(f"[Remotion] props 保存: {props_path}")
        print(f"[Remotion] プロジェクトフォルダ: {REMOTION_PROJECT_DIR}")

        # node_modules が無ければ npm install
        if not (REMOTION_PROJECT_DIR / "node_modules").exists():
            print("[Remotion] npm install 実行中...")
            result = _run(["npm", "install"], cwd=REMOTION_PROJECT_DIR)
            if result.returncode != 0:
                raise RuntimeError("npm install に失敗しました")

        # Chrome の場所を特定
        chrome_path = _find_chrome()
        if chrome_path:
            print(f"[Remotion] Chrome を使用: {chrome_path}")
        else:
            raise FileNotFoundError(
                "Chrome が見つかりません。Google Chrome をインストールしてください。"
            )

        # remotion render 実行
        props_abs = str(props_path.resolve())
        out_abs   = str(out.resolve())
        cmd = [
            "npx", "remotion", "render",
            "SubtitleVideo",
            out_abs,
            "--props",              props_abs,
            "--browser-executable", chrome_path,
            "--concurrency",        "2",
        ]
        if preview:
            cmd += [
                "--scale",         "0.5",
                "--jpeg-quality",  "80",
                "--crf",           "28",
            ]
            print("[Remotion] 🎬 プレビューモード (低解像度・高速)")

        print(f"[Remotion] レンダリング開始...")
        print(f"  出力先: {out_abs}")

        result = _run(cmd, cwd=REMOTION_PROJECT_DIR,
                      extra_env={"NODE_TLS_REJECT_UNAUTHORIZED": "0"})

        if result.returncode != 0:
            raise RuntimeError(f"Remotion レンダリング失敗 (code={result.returncode})")

        print(f"[Remotion] 動画生成完了: {out}")
        return out

    finally:
        # レンダリング完了後にHTTPサーバーを停止
        file_server.shutdown()
        print("[Remotion] 音声配信サーバーを停止しました")
