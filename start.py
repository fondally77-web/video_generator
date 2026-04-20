"""
start.py — ワンコマンドランチャー

使い方:
  python start.py                     → エディタを起動（Streamlit）
  python start.py --run --audio x.wav → パイプライン実行
  python start.py --check             → 環境チェックのみ

自動で行うこと:
  1. Whisper サーバーが起動していなければ自動起動
  2. VOICEVOX の起動確認（未起動なら案内表示）
  3. remotion_project/node_modules が無ければ npm install
  4. 指定されたモード（エディタ or パイプライン）を起動
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

# ── 設定 ──────────────────────────────────────────────
WHISPER_URL   = os.getenv("WHISPER_SERVER_URL", "http://127.0.0.1:8000")
VOICEVOX_URL  = "http://127.0.0.1:50021"
PROJECT_ROOT  = Path(__file__).parent
WHISPER_DIR   = PROJECT_ROOT / "whisper_server"
REMOTION_DIR  = PROJECT_ROOT / "remotion_project"


# ── ヘルスチェック ────────────────────────────────────
def _check(url: str, path: str, timeout: float = 2) -> dict | None:
    try:
        r = requests.get(f"{url}{path}", timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def check_whisper() -> bool:
    info = _check(WHISPER_URL, "/health")
    if info:
        print(f"  ✅ Whisper サーバー稼働中 (model: {info.get('model', '?')})")
        return True
    print("  ❌ Whisper サーバー未起動")
    return False


def check_voicevox() -> bool:
    info = _check(VOICEVOX_URL, "/version")
    if info:
        print(f"  ✅ VOICEVOX 稼働中 (v{info})")
        return True
    print("  ❌ VOICEVOX 未起動")
    return False


def check_node() -> bool:
    try:
        r = subprocess.run("node --version", capture_output=True, text=True, shell=True)
        if r.returncode == 0:
            print(f"  ✅ Node.js {r.stdout.strip()}")
            return True
    except FileNotFoundError:
        pass
    print("  ❌ Node.js が見つかりません")
    return False


def check_azure_env() -> bool:
    ep  = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    key = os.getenv("AZURE_OPENAI_API_KEY", "")
    if ep and "<your-" not in ep and key and "<your-" not in key:
        print(f"  ✅ Azure OpenAI 設定済み")
        return True
    # config.py のデフォルト値をチェック
    try:
        from config import AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY
        if "<your-" not in AZURE_OPENAI_ENDPOINT and "<your-" not in AZURE_OPENAI_API_KEY:
            print(f"  ✅ Azure OpenAI 設定済み（config.py）")
            return True
    except Exception:
        pass
    print("  ⚠️  Azure OpenAI 未設定（環境変数 or config.py を設定してください）")
    return False


# ── 自動起動 ──────────────────────────────────────────
def _ensure_packages(python_cmd: str, req_file: Path) -> str:
    """venv の python にパッケージがインストール済みか確認し、なければ pip install する"""
    r = subprocess.run(
        [python_cmd, "-c", "import fastapi, whisper"],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        return python_cmd

    print(f"  📦 パッケージ未インストール — pip install を実行します")
    if req_file.exists():
        print(f"     （torch + whisper で数分かかる場合があります）")
        r = subprocess.run(
            [python_cmd, "-m", "pip", "install", "-r", str(req_file)],
            cwd=str(WHISPER_DIR),
        )
        if r.returncode != 0:
            print(f"  ❌ pip install 失敗 — 手動で実行してください:")
            print(f"     {python_cmd} -m pip install -r {req_file}")
            return python_cmd  # 失敗してもパスは返す（起動を試みる）
        print(f"  ✅ パッケージインストール完了")
    else:
        print(f"  ⚠️  {req_file} が見つかりません")
    return python_cmd


def _ensure_whisper_venv() -> str | None:
    """
    whisper_server/venv がなければ作成し pip install する。
    成功したら venv 内の python パスを返す。失敗したら None。
    """
    venv_dir = WHISPER_DIR / "venv"
    if os.name == "nt":
        venv_python = venv_dir / "Scripts" / "python.exe"
    else:
        venv_python = venv_dir / "bin" / "python"
    req_file = WHISPER_DIR / "requirements.txt"

    # すでに venv があればパッケージも入っているか確認
    if venv_python.exists():
        return _ensure_packages(str(venv_python), req_file)

    # venv ディレクトリ自体はあるが python が見つからない場合も確認
    scripts_dir = venv_dir / ("Scripts" if os.name == "nt" else "bin")
    if scripts_dir.exists():
        candidates = list(scripts_dir.glob("python*"))
        if candidates:
            return _ensure_packages(str(candidates[0]), req_file)

    print(f"  📦 whisper_server/venv が見つかりません — 自動作成します")

    # venv 作成
    print(f"     1/2: venv 作成中...")
    r = subprocess.run([sys.executable, "-m", "venv", str(venv_dir)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  ❌ venv 作成失敗: {r.stderr[:300]}")
        return None
    # 作成された python を探す
    if not venv_python.exists():
        candidates = list(scripts_dir.glob("python*"))
        if candidates:
            venv_python = candidates[0]
        else:
            print(f"  ❌ venv は作成されたが python が見つかりません: {scripts_dir}")
            return None

    # pip install
    if req_file.exists():
        print(f"     2/2: pip install 実行中（torch + whisper で数分かかります）...")
        r = subprocess.run(
            [str(venv_python), "-m", "pip", "install", "-r", str(req_file)],
            cwd=str(WHISPER_DIR),
        )
        if r.returncode != 0:
            print(f"  ❌ pip install 失敗（手動で確認してください）:")
            print(f"     {venv_python} -m pip install -r {req_file}")
            return None
    else:
        print(f"  ⚠️  {req_file} が見つかりません — pip install をスキップ")

    print(f"  ✅ whisper_server/venv 作成完了")
    return str(venv_python)


def start_whisper() -> bool:
    """Whisper サーバーをバックグラウンドで起動"""
    server_py = WHISPER_DIR / "server.py"
    if not server_py.exists():
        print(f"  ⚠️  {server_py} が見つかりません")
        return False

    # venv を確認・作成
    python_cmd = _ensure_whisper_venv()
    if python_cmd is None:
        # venv 作成失敗 — メイン Python にフォールバック（whisper 入ってなければ失敗する）
        print(f"  ⚠️  venv が使えないためメイン Python で試みます")
        python_cmd = sys.executable

    print(f"  🚀 Whisper サーバーを起動中...")
    print(f"     Python: {python_cmd}")

    # ログファイルに出力（エラー原因を追えるように）
    log_path = WHISPER_DIR / "startup.log"
    log_file = open(log_path, "w", encoding="utf-8")

    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [python_cmd, str(server_py)],
        cwd=str(WHISPER_DIR),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        **kwargs,
    )

    # 起動待ち（最大90秒 — 初回はモデルダウンロードで時間がかかる）
    print(f"     （初回はモデルDLで1〜2分かかる場合があります）")
    for i in range(90):
        time.sleep(1)
        if _check(WHISPER_URL, "/health"):
            print(f"  ✅ Whisper サーバー起動完了（{i+1}秒）")
            log_file.close()
            return True
        if i % 10 == 9:
            print(f"    ... 待機中（{i+1}秒）")

    log_file.close()

    # タイムアウト — ログの末尾を表示して原因を伝える
    print("  ❌ Whisper サーバーの起動がタイムアウトしました")
    try:
        log_tail = log_path.read_text(encoding="utf-8").strip().splitlines()
        if log_tail:
            print(f"  📄 ログ ({log_path}):")
            for line in log_tail[-8:]:
                print(f"     {line}")
    except Exception:
        pass
    print(f"\n  💡 手動で起動してみてください:")
    print(f"     cd whisper_server")
    print(f"     python server.py")
    return False


def ensure_node_modules() -> None:
    """remotion_project/node_modules が無い、または不完全なら npm install"""
    cli_dir = REMOTION_DIR / "node_modules" / "@remotion" / "cli"
    if cli_dir.exists():
        print(f"  ✅ Remotion node_modules あり")
        return
    if (REMOTION_DIR / "node_modules").exists():
        print(f"  📦 @remotion/cli が見つかりません — npm install を再実行します")
    else:
        print(f"  📦 npm install 実行中（初回のみ）...")
    result = subprocess.run(
        "npm install",
        cwd=str(REMOTION_DIR),
        capture_output=True, text=True,
        shell=True,
    )
    if result.returncode == 0:
        print(f"  ✅ npm install 完了")
    else:
        print(f"  ⚠️  npm install 失敗: {result.stderr[:200]}")


# ── メイン ────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Audio→Video Pipeline ランチャー")
    p.add_argument("--run",   action="store_true", help="パイプラインを実行（デフォルトはエディタ起動）")
    p.add_argument("--audio", default=None, help="入力音声ファイル（--run 時に必要）")
    p.add_argument("--target-sec", type=int, default=25, help="目安尺（秒/スライド）")
    p.add_argument("--skip-voicevox", action="store_true", help="VOICEVOX/Remotionをスキップ")
    p.add_argument("--check", action="store_true", help="環境チェックのみ（起動しない）")
    a = p.parse_args()

    print("=" * 50)
    print("🎬 Audio → Video Pipeline ランチャー")
    print("=" * 50)

    # ── 環境チェック ──────────────────────────────────
    print("\n📋 環境チェック:")
    check_azure_env()

    whisper_ok = check_whisper()
    if not whisper_ok and not a.check:
        print("\n  → Whisper サーバーを自動起動します...")
        whisper_ok = start_whisper()

    voicevox_ok = check_voicevox()
    if not voicevox_ok:
        print("  → VOICEVOX アプリを手動で起動してください")
        if not a.skip_voicevox and a.run:
            print("    （--skip-voicevox で音声合成をスキップ可能）")

    check_node()
    ensure_node_modules()

    if a.check:
        print("\n✅ 環境チェック完了")
        return

    # Whisper が必須なのに起動できていない場合は中断
    if not whisper_ok and a.run:
        print("\n❌ Whisper サーバーが起動できなかったため中断します。")
        print("   別ターミナルで手動起動してから再実行してください:")
        print("     cd whisper_server")
        print("     python server.py")
        sys.exit(1)

    # ── 起動 ──────────────────────────────────────────
    if a.run:
        # パイプライン実行
        if not a.audio:
            print("\n❌ --audio を指定してください")
            print("  例: python start.py --run --audio recording.wav")
            sys.exit(1)

        print(f"\n▶️  パイプライン実行: {a.audio}")
        cmd = [sys.executable, "pipeline.py", "--audio", a.audio,
               "--target-sec", str(a.target_sec)]
        if a.skip_voicevox:
            cmd.append("--skip-voicevox")
        result = subprocess.run(cmd)
        sys.exit(result.returncode)
    else:
        # エディタ起動
        print(f"\n🎨 スライドエディタを起動します...")
        print(f"   ブラウザで http://localhost:8501 を開いてください\n")
        result = subprocess.run(
            [sys.executable, "-m", "streamlit", "run", "slide_editor.py"]
        )
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
