# 🎬 Audio → Video Pipeline

音声ファイルからスライド付き動画を自動生成するツール。

Whisper（文字起こし）→ Azure OpenAI（テキスト修正・スライド生成）→ VOICEVOX（音声合成）→ Remotion（動画書き出し）

---

## 📂 プロジェクト構成

```
video_generator-main/
├── start.py               # ← ここから起動（ランチャー）
├── config.py              # 全体設定
├── pipeline.py            # パイプライン本体（CLI）
├── slide_editor.py        # スライドエディタ（Streamlit）
├── requirements.txt
│
├── core/                  # パイプライン処理モジュール
│   ├── transcriber.py     #   Whisper → セグメント取得
│   ├── corrector.py       #   テキスト修正 + 話者判定
│   ├── slide_builder.py   #   スライド構造の自動生成
│   ├── segment_merger.py  #   話題境界でセグメントをマージ
│   ├── voicevox_client.py #   話者別の音声合成
│   └── video_builder.py   #   Remotion レンダリング
│
├── whisper_server/        # Whisper 音声認識サーバー
│   ├── server.py
│   └── requirements.txt
│
└── remotion_project/      # Remotion 動画プロジェクト
    ├── package.json
    └── src/
        └── SubtitleVideo.tsx  # 9種類のスライドレイアウト
```

---

## ⚙️ セットアップ（初回のみ）

### 1. Python パッケージ

```bash
pip install -r requirements.txt
```

Whisper サーバー用（venv 推奨）:
```bash
cd whisper_server
pip install -r requirements.txt
cd ..
```

### 2. Azure OpenAI の設定

環境変数を設定するか、`config.py` を直接編集してください。

```bash
# Windows
set AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/
set AZURE_OPENAI_API_KEY=<your-api-key>
set AZURE_OPENAI_DEPLOYMENT=gpt-4o
```

### 3. 外部ツール

- **VOICEVOX**: https://voicevox.hiroshiba.jp/ からインストール
- **Node.js 18+**: Remotion のレンダリングに必要
- **ffmpeg**: 動画エンコードに必要（`winget install ffmpeg` 等）

`npm install` と Whisper サーバーの起動は `start.py` が自動で行います。

---

## 🚀 使い方

### エディタで編集（GUI）

```bash
python start.py
```

#### Video Previewモード
サイドバーで「Video Preview」を選択すると、レンダリング前にスライドの流れ・タイミングを確認できます。
VOICEVOX 実行後の JSON を読み込んでいれば音声付きでプレビュー再生されます。
開始スライドとプレビュー枚数をサイドバーで指定可能。

#### テキスト拡大率・リスト項目・カスタムCSS
Slide Editorモードで各セグメント編集パネルから以下が調整できます：
- テキスト拡大率: CSS zoom で 0.5×～2.0× に拡大縮小
- リスト項目: question / feature / flow / bullets / split / cards / timeline レイアウトのタグ・箇条書き
- カスタムCSS: `.slide` に対する自由なCSS上書き（padding, font-size, background など）

→ Whisper の起動確認・npm install を自動実行後、Streamlit エディタが起動します。

### パイプライン実行（CLI）

```bash
python start.py --run --audio your_audio.wav
```

→ 文字起こし → テキスト修正 → スライド生成 → マージ → VOICEVOX → 動画の全工程を実行します。

### 環境チェックだけ

```bash
python start.py --check
```

### その他のオプション

```bash
# VOICEVOX / Remotion をスキップ（スライドの確認だけしたい場合）
python start.py --run --audio your_audio.wav --skip-voicevox

# 1スライドの目安尺を変更（デフォルト 15秒）
python start.py --run --audio your_audio.wav --target-sec 10
```

途中再開（pipeline.py を直接使用）:

```bash
python pipeline.py --audio your_audio.wav --resume output/segments_corrected.json
python pipeline.py --audio your_audio.wav --resume-merged output/segments_merged.json
```

---

## ⚙️ カスタマイズ（config.py）

> **💡 エディタの設定パネルから GUI で変更可能です。**
> サイドバーの「⚙️ 設定」セクションで VOICEVOX の話者・音声パラメータ、動画の解像度・色・フォントサイズを変更し、「💾 設定を保存」で `config_overrides.json` に保存されます。次回のパイプライン実行から自動で反映されます。

### Azure OpenAI

| 設定 | デフォルト | 環境変数 |
|------|-----------|----------|
| `AZURE_OPENAI_ENDPOINT` | — | `AZURE_OPENAI_ENDPOINT` |
| `AZURE_OPENAI_API_KEY` | — | `AZURE_OPENAI_API_KEY` |
| `AZURE_OPENAI_DEPLOYMENT` | `gpt-4o` | `AZURE_OPENAI_DEPLOYMENT` |
| `AZURE_OPENAI_API_VERSION` | `2024-02-15-preview` | — |

### Whisper サーバー

| 設定 | デフォルト | 説明 |
|------|-----------|------|
| `WHISPER_SERVER_URL` | `http://127.0.0.1:8000` | 環境変数でも変更可 |
| `WHISPER_LANGUAGE` | `ja` | 認識言語 |

server.py 側の環境変数:

| 環境変数 | デフォルト | 説明 |
|----------|-----------|------|
| `WHISPER_MODEL` | `small` | `tiny` / `small` / `medium` / `large-v3` |
| `WHISPER_PORT` | `8000` | サーバーポート |

### VOICEVOX

| 設定 | デフォルト | 説明 |
|------|-----------|------|
| `VOICEVOX_SPEAKER_ID` | `3`（ずんだもん） | 話者1人の場合のデフォルト |
| `VOICEVOX_SPEAKERS` | `{"A": 3, "B": 2}` | 話者ラベル→スピーカーID |

主なスピーカーID: 3=ずんだもん, 2=四国めたん, 8=春日部つむぎ, 13=青山龍星

### 動画

| 設定 | デフォルト | 説明 |
|------|-----------|------|
| `VIDEO_FPS` | `30` | フレームレート |
| `VIDEO_WIDTH` × `VIDEO_HEIGHT` | `1920` × `1080` | 動画解像度 |
| `BACKGROUND_COLOR` | `#FFFFFF` | 背景色 |
| `FONT_COLOR` | `#000000` | テキスト色 |
| `HIGHLIGHT_COLOR` | `#000000` | 強調色 |
| `FONT_SIZE` | `64` | 字幕フォントサイズ |

---

## 🔍 トラブルシューティング

| 症状 | 対処 |
|------|------|
| Whisper に接続できない | `start.py` が自動起動を試みます。失敗する場合は `cd whisper_server && python server.py` で手動起動 |
| VOICEVOX に接続できない | VOICEVOX アプリを手動で起動してください |
| Remotion が失敗する | Node.js 18+ と ffmpeg がインストールされているか確認 |
| GPU メモリ不足 | `set WHISPER_MODEL=small`（または `tiny`）で軽量モデルに変更 |
| スライドの質が悪い | `streamlit run slide_editor.py` でプレビューを見ながら手動修正 |
