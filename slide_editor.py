"""
slide_editor.py — ビジュアルプレビュー付きスライドエディタ
使い方: streamlit run slide_editor.py

機能:
  - SubtitleVideo.tsx のデザインを忠実に再現した HTML プレビュー
  - レイアウト変更の即時プレビュー反映
  - Whisper / VOICEVOX サーバーの起動・停止・状態監視
  - スライドの順序入れ替え / 削除 / 追加
  - pipeline.py の各ステップをワンクリック実行
"""
import json
import re
import subprocess
import time
from pathlib import Path
from textwrap import dedent

import requests
import streamlit as st

# 最初の Streamlit コマンドとしてページ設定
st.set_page_config(page_title='スライドエディタ v2', page_icon='🎨', layout='wide')

LAYOUTS = [
    ("title",    "📌 タイトル",         "動画冒頭・章のタイトル"),
    ("question", "❓ 疑問提示",         "問いかけ・課題提示"),
    ("section",  "🟡 セクション",       "黄色背景＋大きな番号"),
    ("feature",  "💡 用語解説",         "用語定義・キーワード解説（デフォルト）"),
    ("split",    "⚖️ 2カラム比較",      "2つの概念を並べて比較"),
    ("flow",     "➡️ フロー図",         "3ステップのプロセス"),
    ("timeline", "📅 タイムライン",     "年表・日付の横軸"),
    ("bullets",  "📋 箇条書き",         "3〜4項目のリスト"),
    ("cards",    "🗂️ カードグリッド",   "01〜06の章立てカード"),
]
LAYOUT_KEYS   = [l[0] for l in LAYOUTS]
LAYOUT_LABELS = {l[0]: f"{l[1]}  —  {l[2]}" for l in LAYOUTS}
LAYOUT_OPTIONS = [LAYOUT_LABELS[k] for k in LAYOUT_KEYS]
DEFAULT_JSON = Path("output/segments_with_slides.json")
OVERRIDES_FILE = Path("config_overrides.json")

WHISPER_URL  = "http://127.0.0.1:8000"
VOICEVOX_URL = "http://127.0.0.1:50021"


# ═══════════════════════════════════════════════════════
#  ビジュアル設定（config_overrides.json から動的に読み込み）
# ═══════════════════════════════════════════════════════

VIEW_DEFAULTS = {
    "TITLE_SCALE": 1.0,
    "LAYOUT_SUB_SCALES":     {k: 1.0 for k in LAYOUT_KEYS},
    "LAYOUT_ITEM_SCALES":    {k: 1.0 for k in LAYOUT_KEYS},
    "LAYOUT_PADDING_SCALES": {k: 1.0 for k in LAYOUT_KEYS},
    "SHOW_TITLE_BRAND": False,
    "BRAND_TEXT": "NotebookLM",
    "TEXT_COLOR_PRIMARY": "#111111",
    "TEXT_COLOR_SUB":     "#888888",
    "TEXT_COLOR_ACCENT":  "#FBCB3E",
    "BACKGROUND_COLOR":   "#FFFFFF",
}


def _load_view_settings() -> dict:
    """config_overrides.json から表示用の設定だけを取り出す。
    ファイルが無い／読めない場合はデフォルトを返す。
    `_PREVIEW_OVERRIDE`（ライブプレビュー用）が設定されていれば最後にマージする。"""
    settings = {k: (v.copy() if isinstance(v, dict) else v) for k, v in VIEW_DEFAULTS.items()}
    if OVERRIDES_FILE.exists():
        try:
            data = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = None
        if data:
            for key in settings:
                if key in data:
                    if isinstance(settings[key], dict) and isinstance(data[key], dict):
                        settings[key].update(data[key])
                    else:
                        settings[key] = data[key]
    # ライブプレビュー用の一時オーバーライドを最後に適用
    if _PREVIEW_OVERRIDE:
        for key, val in _PREVIEW_OVERRIDE.items():
            if isinstance(settings.get(key), dict) and isinstance(val, dict):
                settings[key] = {**settings[key], **val}
            else:
                settings[key] = val
    return settings


# ライブプレビュー時のみセットされる一時オーバーライド（dict or None）
_PREVIEW_OVERRIDE: dict | None = None


def _render_with_overrides(seg: dict, overrides: dict, show_progress_bar: bool = False) -> str:
    """一時的に view settings を上書きして HTML をレンダリング（保存せずに反映）"""
    global _PREVIEW_OVERRIDE
    prev = _PREVIEW_OVERRIDE
    _PREVIEW_OVERRIDE = overrides
    try:
        return render_preview_html(seg, show_progress_bar=show_progress_bar)
    finally:
        _PREVIEW_OVERRIDE = prev


# ═══════════════════════════════════════════════════════
#  HTML プレビュー生成（SubtitleVideo.tsx を忠実に再現）
# ═══════════════════════════════════════════════════════

# 共通 CSS（Remotion のデザイン定数を再現）
PREVIEW_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@300;400;700;900&display=swap');
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:'Noto Sans JP','Hiragino Kaku Gothic ProN','Yu Gothic',sans-serif; }
.slide { position:relative; width:1920px; height:1080px; overflow:hidden;
         background:#fff; }
.slide * { font-family:inherit; }
/* スケーリングラッパー */
.scale-wrap { transform-origin:top left; overflow:hidden;
              border-radius:8px; border:1px solid #e0e0e0; }
/* グリッド背景 */
.grid-bg { position:absolute; inset:0; }
.grid-bg svg { width:100%; height:100%; }
/* プログレスバー */
.bar { position:absolute; bottom:0; left:0; right:0; height:3px; background:#E8E8E0; }
.bar-fill { height:100%; width:35%; background:#FBCB3E; }
/* 黄色影カード */
.ycard { position:relative; }
.ycard-shadow { position:absolute; inset:0; background:#FBCB3E; border-radius:12px;
                border:2px solid #111; transform:translate(4px,4px); }
.ycard-body { position:relative; background:#fff; border-radius:12px; border:2px solid #111;
              padding:20px 24px; }
.icon { font-size:32px; margin-bottom:8px; }
.mg { color:#888; }
.y  { color:#FBCB3E; }
.b  { color:#111; }
"""

GRID_SVG = """<svg class="grid-bg" viewBox="0 0 1920 1080" preserveAspectRatio="none">
""" + "".join(
    f'<line x1="{i*36}" y1="0" x2="{i*36}" y2="1080" stroke="#EDEDED" stroke-width="0.8"/>'
    for i in range(54)
) + "".join(
    f'<line x1="0" y1="{i*36}" x2="1920" y2="{i*36}" stroke="#EDEDED" stroke-width="0.8"/>'
    for i in range(31)
) + "</svg>"

BAR_HTML = '<div class="bar"><div class="bar-fill"></div></div>'

# ─── vw → px 変換（プレビュー用）───────────────────────
# プレビューは1920×1080固定で描画し、CSS transformで縮小表示する。
# そのためレンダラーが出力する vw 値を px に変換する必要がある。
_VW_PX = 19.2  # 1vw = 19.2px（1920px ÷ 100）
_VW_RE = re.compile(r'([\d.]+)vw')

def _vw_to_px(html: str) -> str:
    """HTML内の全ての vw 値を px に変換（1920px基準）"""
    return _VW_RE.sub(lambda m: f'{float(m.group(1)) * _VW_PX:.1f}px', html)


SPEAKER_COLORS = {
    "A": "#F59E0B",
    "B": "#6366F1",
    "C": "#10B981",
    "D": "#EC4899",
    "UNKNOWN": "#9CA3AF",
}


def _speaker_badge_html(speaker: str) -> str:
    """speaker ラベルを正規化し、カラー付きバッジ HTML を返す"""
    name = (speaker or "unknown").strip()
    label = name.upper() if name else "UNKNOWN"
    color = SPEAKER_COLORS.get(label, SPEAKER_COLORS["UNKNOWN"])
    if label == "UNKNOWN":
        return (
            f'<span style="display:inline-flex;align-items:center;gap:4px;'
            f'padding:0.2em 0.7em;border-radius:999px;font-size:0.8em;'
            f'background:#F3F4F6;border:1px dashed #9CA3AF;color:#4B5563;">'
            f'{label}</span>'
        )
    return (
        f'<span style="display:inline-flex;align-items:center;gap:4px;'
        f'padding:0.2em 0.7em;border-radius:999px;font-size:0.8em;'
        f'background:{color};color:#fff;font-weight:600;border:1px solid rgba(255,255,255,0.6);">'
        f'{label}</span>'
    )


def _auto_fs(text: str, max_px: float, min_px: float) -> float:
    """SubtitleVideo.tsx の autoFs を再現（ビューポート比でスケール）"""
    l = len(text or "")
    fs = max_px if l <= 8 else max_px * 0.85 if l <= 14 else max_px * 0.7 if l <= 20 else max_px * 0.58
    return max(min_px, round(fs, 1))


def _layout_fs(text: str, max_px: float, min_px: float,
               extra_scale: float = 1.0) -> float:
    """_auto_fs に要素別スケールを掛ける"""
    return _auto_fs(text, max_px * extra_scale, min_px * extra_scale)


def _get_scales(seg: dict, settings: dict | None = None) -> tuple[float, float, float, float]:
    """seg / settings から (title, sub, item, padding) スケールを取得。
    seg に "_title_scale" 等があればそれを優先（Template Editor のライブプレビュー用）、
    無ければ settings（保存済みオーバーライド）から取得する。"""
    s = settings or _load_view_settings()
    layout = seg.get("slide_layout", "feature")
    ts = seg.get("_title_scale",   s.get("TITLE_SCALE", 1.0))
    ss = seg.get("_sub_scale",     s.get("LAYOUT_SUB_SCALES",     {}).get(layout, 1.0))
    ist = seg.get("_item_scale",   s.get("LAYOUT_ITEM_SCALES",    {}).get(layout, 1.0))
    pad = seg.get("_pad_scale",    s.get("LAYOUT_PADDING_SCALES", {}).get(layout, 1.0))
    return float(ts), float(ss), float(ist), float(pad)


def _get_colors(settings: dict | None = None) -> tuple[str, str, str, str]:
    """(primary, sub, accent, background) を返す"""
    s = settings or _load_view_settings()
    return (
        s.get("TEXT_COLOR_PRIMARY", "#111111"),
        s.get("TEXT_COLOR_SUB",     "#888888"),
        s.get("TEXT_COLOR_ACCENT",  "#FBCB3E"),
        s.get("BACKGROUND_COLOR",   "#FFFFFF"),
    )


def _pad(base_pct: float, scale: float) -> str:
    """元のパーセント余白に scale を掛けた CSS 値を返す（最低0%）"""
    return f"{max(0.0, base_pct * scale):.2f}%"


def _format_speaker_runs(seg: dict) -> str:
    runs = seg.get("speaker_runs") or []
    formatted: list[str] = []
    for run in runs:
        text = (run.get("text") or "").strip()
        if not text:
            continue
        speaker = (run.get("speaker") or "unknown").strip().upper() or "UNKNOWN"
        formatted.append(f"{speaker}：{text}")
    return "<br/>".join(formatted)


def _ycard(inner_html: str, accent: str = "#FBCB3E", primary: str = "#111111") -> str:
    return f"""<div class="ycard">
      <div class="ycard-shadow" style="background:{accent};border-color:{primary}"></div>
      <div class="ycard-body" style="border-color:{primary}">{inner_html}</div>
    </div>"""


def _render_title(seg: dict) -> str:
    s = _load_view_settings()
    ts, ss, _, pad = _get_scales(seg, s)
    primary, sub_color, accent, _bg = _get_colors(s)
    fs = _layout_fs(seg.get("slide_title", ""), 5.2, 2.8, ts)
    sub = seg.get("slide_sub", "")
    sub_fs = round(1.8 * ss, 2)
    sub_html = f'<div style="font-size:{sub_fs}vw;color:{sub_color};margin-top:12px">{sub}</div>' if sub else ""
    show_brand = s.get("SHOW_TITLE_BRAND", False)
    brand_text = s.get("BRAND_TEXT", "NotebookLM")
    brand_html = (
        f'<div style="font-size:1.2vw;font-weight:300;color:{sub_color};letter-spacing:0.2em;'
        f'margin-bottom:10px">{brand_text}</div>'
        if show_brand and brand_text else ""
    )
    return f"""{GRID_SVG}
    <div style="position:absolute;inset:0;display:flex;flex-direction:column;
                align-items:center;justify-content:center;text-align:center;padding:0 {_pad(8.0, pad)}">
      {brand_html}
      <div style="width:15vw;height:1.5px;background:{primary};opacity:.5;margin-bottom:16px"></div>
      <div style="font-size:{fs}vw;font-weight:900;color:{primary};line-height:1.3">
        {seg.get("slide_title","")}</div>
      {sub_html}
      <div style="width:15vw;height:1.5px;background:{primary};opacity:.5;margin-top:16px"></div>
    </div>{BAR_HTML}"""


def _render_question(seg: dict) -> str:
    s = _load_view_settings()
    ts, ss, ist, pad = _get_scales(seg, s)
    primary, sub_color, accent, _bg = _get_colors(s)
    fs = _layout_fs(seg.get("slide_title", ""), 4.2, 2.4, ts)
    icon = seg.get("slide_icon", "") or "？"
    sub = seg.get("slide_sub", "")
    sub_html = f'<div style="font-size:{fs*0.45*ss:.1f}vw;color:{sub_color};margin-top:10px">{sub}</div>' if sub else ""
    items = seg.get("slide_items", [])
    items_html = ""
    if items:
        tags = "".join(
            f'<span style="background:#FEF3C0;border:1.5px solid {accent};border-radius:8px;'
            f'padding:3px 12px;font-size:{fs*0.35*ist:.1f}vw;font-weight:700;color:{primary}">{it}</span>'
            for it in items[:4]
        )
        items_html = f'<div style="display:flex;gap:10px;margin-top:16px;flex-wrap:wrap">{tags}</div>'
    return f"""{GRID_SVG}
    <div style="position:absolute;top:50%;left:55%;transform:translate(-40%,-50%);
                font-size:32vw;font-weight:900;color:{accent};opacity:.45;line-height:1">
      {icon}</div>
    <div style="position:absolute;inset:0;display:flex;flex-direction:column;justify-content:center;padding:0 {_pad(7.0, pad)}">
      <div style="font-size:{fs}vw;font-weight:900;color:{primary};line-height:1.4;max-width:80%">
        {seg.get("slide_title","")}</div>
      {sub_html}
      {items_html}
    </div>{BAR_HTML}"""


def _render_section(seg: dict) -> str:
    s = _load_view_settings()
    ts, ss, _, pad = _get_scales(seg, s)
    primary, sub_color, accent, _bg = _get_colors(s)
    fs = _layout_fs(seg.get("slide_title", ""), 4.0, 2.2, ts)
    num = seg.get("slide_number", "") or "1"
    sub = seg.get("slide_sub", "")
    sub_fs = round(1.6 * ss, 2)
    sub_html = f'<div style="font-size:{sub_fs}vw;color:{sub_color};margin-top:8px">{sub}</div>' if sub else ""
    return f"""<div style="position:absolute;inset:0;background:{accent}"></div>
    <div style="position:absolute;inset:0;display:flex;align-items:center;padding:0 {_pad(5.0, pad)};gap:3%">
      <div style="font-size:18vw;font-weight:900;color:#fff;line-height:1;
                  -webkit-text-stroke:3px {primary}">{num}</div>
      <div style="flex:1">
        <div style="background:#fff;border-radius:16px;border:2.5px solid {primary};padding:24px 30px">
          <div style="font-size:{fs}vw;font-weight:900;color:{primary};line-height:1.3">
            {seg.get("slide_title","")}</div>
          {sub_html}
        </div>
      </div>
    </div>{BAR_HTML}"""


def _render_feature(seg: dict) -> str:
    s = _load_view_settings()
    ts, ss, ist, pad = _get_scales(seg, s)
    primary, sub_color, accent, _bg = _get_colors(s)
    fs = _layout_fs(seg.get("slide_title", ""), 4.2, 2.4, ts)
    sub_fs = max(1.4, round(fs * 0.45 * ss, 1))
    item_fs = max(1.4, round(fs * 0.45 * ist, 1))
    icon = seg.get("slide_icon", "")
    icon_html = f'<div class="icon">{icon}</div>' if icon else ""
    sub = seg.get("slide_sub", "")
    sub_html = f'<div style="font-size:{sub_fs}vw;color:{primary};line-height:1.7;margin-top:4px">{sub}</div>' if sub else ""
    items = seg.get("slide_items", [])
    items_html = "".join(
        f'<div style="display:flex;gap:8px;margin-top:6px">'
        f'<span style="color:{accent};font-weight:900;font-size:{item_fs}vw">•</span>'
        f'<span style="font-size:{item_fs}vw;color:{primary}">{it}</span></div>'
        for it in items
    )
    inner = f"""{icon_html}
      <div style="font-size:{fs}vw;font-weight:900;color:{primary};line-height:1.3;margin-bottom:8px">
        {seg.get("slide_title","")}</div>
      {sub_html}{items_html}"""
    return f"""{GRID_SVG}
    <div style="position:absolute;inset:0;display:flex;align-items:center;
                justify-content:center;padding:{_pad(4.0, pad)} {_pad(6.0, pad)}">
      <div style="width:100%;max-width:90%">{_ycard(inner, accent, primary)}</div>
    </div>{BAR_HTML}"""


def _render_split(seg: dict) -> str:
    s = _load_view_settings()
    ts, _, ist, pad = _get_scales(seg, s)
    primary, _sub_color, accent, _bg = _get_colors(s)
    items = seg.get("slide_items", [])
    if len(items) < 2:
        items = [seg.get("slide_title", ""), seg.get("slide_sub", "") or ""]
    h_fs = _layout_fs(seg.get("slide_title", ""), 2.6, 1.6, ts)
    i_fs = _layout_fs(items[0], 1.9, 1.3, ist)
    icons = ["📊", "📄"]
    cards = ""
    for idx, it in enumerate(items[:2]):
        inner = (f'<div style="font-size:2.8vw;margin-bottom:6px">{icons[idx]}</div>'
                 f'<div style="font-size:{i_fs}vw;font-weight:700;color:{primary};line-height:1.6">{it}</div>')
        cards += f'<div style="flex:1">{_ycard(inner, accent, primary)}</div>'
    return f"""{GRID_SVG}
    <div style="position:absolute;inset:0;display:flex;flex-direction:column;padding:{_pad(4.0, pad)} {_pad(5.0, pad)}">
      <div style="font-size:{h_fs}vw;font-weight:700;color:{primary};margin-bottom:20px">
        {seg.get("slide_title","")}</div>
      <div style="display:flex;gap:24px;flex:1;align-items:stretch">{cards}</div>
    </div>{BAR_HTML}"""


def _render_flow(seg: dict) -> str:
    s = _load_view_settings()
    ts, _, ist, pad = _get_scales(seg, s)
    primary, _sub_color, accent, _bg = _get_colors(s)
    items = seg.get("slide_items", [])
    if len(items) < 2:
        items = [seg.get("slide_title", ""), "確認", "完了"]
    h_fs = _layout_fs(seg.get("slide_title", ""), 2.6, 1.6, ts)
    i_fs = _layout_fs(items[0], 1.8, 1.2, ist)
    steps = ""
    for idx, it in enumerate(items[:3]):
        steps += (f'<div style="background:#fff;border-radius:10px;border:1.5px solid {primary};'
                  f'padding:16px 18px;flex:1;min-height:80px">'
                  f'<div style="font-size:{i_fs}vw;font-weight:700;color:{primary};line-height:1.5">{it}</div>'
                  f'</div>')
        if idx < min(len(items), 3) - 1:
            steps += f'<div style="color:{accent};font-size:2.8vw;font-weight:900;display:flex;align-items:center">→</div>'
    return f"""{GRID_SVG}
    <div style="position:absolute;inset:0;display:flex;flex-direction:column;padding:{_pad(3.0, pad)} {_pad(5.0, pad)}">
      <div style="font-size:{h_fs}vw;font-weight:700;color:{primary};margin-bottom:18px">
        {seg.get("slide_title","")}</div>
      <div style="flex:1;display:flex;align-items:center;background:#FEF3C0;
                  border-radius:14px;padding:20px 30px;gap:12px">{steps}</div>
    </div>{BAR_HTML}"""


def _render_timeline(seg: dict) -> str:
    s = _load_view_settings()
    ts, _, ist, pad = _get_scales(seg, s)
    primary, sub_color, accent, _bg = _get_colors(s)
    items = seg.get("slide_items", [])
    if len(items) < 2:
        items = ["2019年: 開始", "2025年: 移行", "2027年: 完全適用"]
    h_fs = _layout_fs(seg.get("slide_title", ""), 2.6, 1.6, ts)
    n = min(len(items), 4)
    label_fs = round(1.9 * ist, 2)
    desc_fs  = round(1.7 * ist, 2)
    dots = ""
    for idx, it in enumerate(items[:n]):
        parts = it.split(":", 1) if ":" in it else it.split("：", 1) if "：" in it else [it, ""]
        label = parts[0].strip()
        desc  = parts[1].strip() if len(parts) > 1 else ""
        pct   = 10 + idx * (80 / max(n - 1, 1))
        dots += f"""<div style="position:absolute;top:14px;left:{pct}%;transform:translateX(-50%);
                     width:12px;height:12px;border-radius:50%;background:{accent};
                     border:2px solid {primary}"></div>
        <div style="position:absolute;top:36px;left:{pct}%;transform:translateX(-50%);text-align:center;width:22%">
          <div style="font-size:{label_fs}vw;font-weight:900;color:{primary};border:1.5px solid {primary};
                      border-radius:6px;padding:3px 10px;display:inline-block;background:#fff">{label}</div>
          <div style="font-size:{desc_fs}vw;color:{sub_color};margin-top:6px;line-height:1.4">{desc}</div>
        </div>"""
    return f"""{GRID_SVG}
    <div style="position:absolute;inset:0;display:flex;flex-direction:column;padding:{_pad(4.0, pad)} {_pad(5.0, pad)}">
      <div style="font-size:{h_fs}vw;font-weight:700;color:{primary};margin-bottom:5vw">
        {seg.get("slide_title","")}</div>
      <div style="position:relative;height:40%">
        <div style="position:absolute;top:19px;left:5%;width:90%;height:2px;background:{accent}"></div>
        {dots}
      </div>
    </div>{BAR_HTML}"""


def _render_bullets(seg: dict) -> str:
    s = _load_view_settings()
    ts, _, ist, pad = _get_scales(seg, s)
    primary, _sub_color, _accent, _bg = _get_colors(s)
    items = seg.get("slide_items", [])
    if not items:
        items = [seg.get("slide_sub", "")] if seg.get("slide_sub") else []
    h_fs = _layout_fs(seg.get("slide_title", ""), 3.2, 2.1, ts)
    i_fs = _layout_fs(items[0] if items else "", 2.0, 1.3, ist)
    bullets = ""
    for it in items[:4]:
        bullets += (f'<div style="display:flex;align-items:flex-start;gap:12px;margin-bottom:10px">'
                    f'<div style="width:8px;height:8px;border-radius:50%;background:{primary};'
                    f'margin-top:{i_fs*0.55:.1f}vw;flex-shrink:0"></div>'
                    f'<div style="font-size:{i_fs}vw;color:{primary};line-height:1.7">{it}</div></div>')
    return f"""{GRID_SVG}
    <div style="position:absolute;inset:0;display:flex;flex-direction:column;padding:{_pad(5.0, pad)} {_pad(6.0, pad)}">
      <div style="font-size:{h_fs}vw;font-weight:900;color:{primary};margin-bottom:28px;line-height:1.3">
        {seg.get("slide_title","")}</div>
      <div style="display:flex;flex-direction:column;gap:4px">{bullets}</div>
    </div>{BAR_HTML}"""


_CARD_NUM_RE = re.compile(r'^\d+[:：]\s*')


def _render_cards(seg: dict) -> str:
    s = _load_view_settings()
    ts, _, ist, pad = _get_scales(seg, s)
    primary, _sub_color, _accent, _bg = _get_colors(s)
    items = seg.get("slide_items", [])
    if len(items) < 3:
        return _render_feature(seg)
    h_fs = _layout_fs(seg.get("slide_title", ""), 3.0, 1.8, ts)
    COLORS = ["#DBEAFE", "#FEF3C0", "#D1FAE5", "#FDE2E2", "#E8DAEF", "#FCE4EC"]
    BORDERS = ["#3B82F6", "#F59E0B", "#10B981", "#EF4444", "#8B5CF6", "#EC4899"]
    cards = ""
    for idx, it in enumerate(items[:6]):
        cleaned = _CARD_NUM_RE.sub("", it)
        parts = cleaned.split(":", 1) if ":" in cleaned else cleaned.split("：", 1) if "：" in cleaned else [cleaned, ""]
        title_part = parts[0].strip()
        desc_part  = parts[1].strip() if len(parts) > 1 else ""
        i_fs = _layout_fs(title_part, 1.8, 1.3, ist)
        num = str(idx + 1).zfill(2)
        bg = COLORS[idx % len(COLORS)]
        border = BORDERS[idx % len(BORDERS)]
        desc_html = f'<div style="font-size:{i_fs*0.75:.1f}vw;color:#555;margin-top:4px">{desc_part}</div>' if desc_part else ""
        cards += (f'<div style="width:calc(33.3% - 12px)">'
                  f'<div style="background:{bg};border-radius:14px;border:2px solid {border};padding:16px 14px;min-height:80px">'
                  f'<div style="font-size:2.4vw;font-weight:900;color:{border};opacity:.7;margin-bottom:6px">{num}</div>'
                  f'<div style="font-size:{i_fs}vw;font-weight:700;color:{primary};line-height:1.5">{title_part}</div>'
                  f'{desc_html}</div></div>')
    return f"""{GRID_SVG}
    <div style="position:absolute;inset:0;display:flex;flex-direction:column;padding:{_pad(4.0, pad)} {_pad(5.0, pad)}">
      <div style="font-size:{h_fs}vw;font-weight:900;color:{primary};margin-bottom:16px;line-height:1.3">
        {seg.get("slide_icon","")} {seg.get("slide_title","")}</div>
      <div style="display:flex;flex-wrap:wrap;gap:14px;flex:1;align-content:flex-start">
        {cards}</div>
    </div>{BAR_HTML}"""


RENDERERS = {
    "title":    _render_title,
    "question": _render_question,
    "section":  _render_section,
    "feature":  _render_feature,
    "split":    _render_split,
    "flow":     _render_flow,
    "timeline": _render_timeline,
    "bullets":  _render_bullets,
    "cards":    _render_cards,
}


def render_preview_html(seg: dict, show_progress_bar: bool = True) -> str:
    """スライドプレビューのHTMLを生成
    1920×1080 固定サイズで描画し、JavaScript で縮小表示する。
    vw 単位は px に一括変換してから出力する。
    show_progress_bar=False で黄色プログレスバー（Remotion再生用）を除去。
    """
    layout = seg.get("slide_layout", "feature")
    renderer = RENDERERS.get(layout, _render_feature)
    body = renderer(seg)
    # vw → px 変換（1920px基準）
    body = _vw_to_px(body)
    if not show_progress_bar:
        body = body.replace(BAR_HTML, "")

    _, _, _, bg = _get_colors()

    return (
        f"<html><head><style>{PREVIEW_CSS}"
        f"*{{margin:0;padding:0;box-sizing:border-box;}}"
        f"html,body{{width:100%;height:100%;overflow:hidden;background:transparent;}}"
        f"#preview-root{{position:fixed;inset:0;display:flex;"
        f"align-items:center;justify-content:center;}}"
        f"#scale-wrap{{width:1920px;height:1080px;flex:0 0 auto;"
        f"transform-origin:center center;}}"
        f".slide{{background:{bg} !important;}}"
        f"</style></head>"
        f'<body><div id="preview-root">'
        f'<div id="scale-wrap"><div class="slide">{body}</div></div>'
        f'</div>'
        f'<script>'
        f'(function(){{'
        f'var sw=document.getElementById("scale-wrap");'
        f'function fit(){{'
        f'var w=window.innerWidth||document.documentElement.clientWidth;'
        f'var h=window.innerHeight||document.documentElement.clientHeight;'
        f'if(!w||!h)return;'
        f'var s=Math.min(w/1920,h/1080);'
        f'sw.style.transform="scale("+s+")";'
        f'}}'
        f'fit();'
        f'window.addEventListener("resize",fit);'
        f'window.addEventListener("load",fit);'
        f'if(window.ResizeObserver){{'
        f'try{{new ResizeObserver(fit).observe(document.documentElement);}}catch(e){{}}'
        f'}}'
        f'setTimeout(fit,50);setTimeout(fit,200);setTimeout(fit,800);'
        f'}})();'
        f'</script>'
        f'</body></html>'
    )


# ═══════════════════════════════════════════════════════
#  サーバー制御
# ═══════════════════════════════════════════════════════

def _check_server(url: str, path: str = "/health", timeout: float = 2) -> dict | None:
    try:
        r = requests.get(f"{url}{path}", timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# ═══════════════════════════════════════════════════════
#  レンダリング済み動画ビューア
# ═══════════════════════════════════════════════════════

_RESULT_PREVIEW_PATH = Path("output/result_preview.mp4")
_RESULT_FINAL_PATH   = Path("output/result.mp4")


def _format_mtime(path: Path) -> str:
    """ファイルの更新時刻を '2026-04-21 14:32 (5 分前)' の形式で返す。"""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ""
    abs_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
    delta = int(time.time() - mtime)
    if delta < 60:
        rel = f"{delta} 秒前"
    elif delta < 3600:
        rel = f"{delta // 60} 分前"
    elif delta < 86400:
        h = delta // 3600
        m = (delta % 3600) // 60
        rel = f"{h} 時間 {m} 分前" if m else f"{h} 時間前"
    else:
        rel = f"{delta // 86400} 日前"
    return f"{abs_str} ({rel})"


def _render_rendered_video_section() -> None:
    """Video Preview モードの画面先頭に「📺 レンダリング済み動画」を表示する。"""
    st.subheader("📺 レンダリング済み動画")

    has_preview = _RESULT_PREVIEW_PATH.exists()
    has_final   = _RESULT_FINAL_PATH.exists()

    if not has_preview and not has_final:
        st.info("まだレンダリングされていません。サイドバーの「⚡ パイプライン実行」から生成してください。")
        st.divider()
        return

    # 表示切替（両方ある場合のみラジオ）
    if has_preview and has_final:
        choice = st.radio(
            "表示中",
            ["プレビュー(低解像度)", "本番(1920x1080)"],
            horizontal=True,
            key="rendered_video_choice",
        )
        target = _RESULT_PREVIEW_PATH if choice.startswith("プレビュー") else _RESULT_FINAL_PATH
    elif has_preview:
        target = _RESULT_PREVIEW_PATH
    else:
        target = _RESULT_FINAL_PATH

    st.video(str(target))

    # 更新時刻
    meta_lines = []
    if has_preview:
        meta_lines.append(f"プレビュー版: {_format_mtime(_RESULT_PREVIEW_PATH)}")
    if has_final:
        meta_lines.append(f"本番版: {_format_mtime(_RESULT_FINAL_PATH)}")
    if meta_lines:
        st.caption("  \n".join(meta_lines))

    st.divider()


# ═══════════════════════════════════════════════════════
#  Streamlit UI
# ═══════════════════════════════════════════════════════

# ─── セッション初期化 ────────────────────────────────────
if "segments" not in st.session_state:
    st.session_state.segments = []
if "loaded" not in st.session_state:
    st.session_state.loaded = False

# ─── モード切替 ──────────────────────────────────────────
mode = st.sidebar.radio("モード", ["Slide Editor", "Video Preview", "Template Editor"])
if mode == "Video Preview":
    if not st.session_state.loaded or not st.session_state.segments:
        st.warning("スライドデータが読み込まれていません。"
                   "まず Slide Editor モードで JSON を読み込んでから切り替えてください。")
        st.stop()

    import base64 as _b64

    _render_rendered_video_section()

    segs_vp = st.session_state.segments
    total = len(segs_vp)
    st.title("▶️ Video Preview（音声付き）")

    # ─── コントロール ────────────────────────────────────
    vc1, vc2 = st.sidebar.columns(2)
    start_idx = vc1.number_input("開始スライド", 1, total, 1) - 1
    n_preview = vc2.number_input("枚数", 1, min(5, total - start_idx), min(2, total - start_idx))
    preview_segs = segs_vp[start_idx : start_idx + n_preview]

    # ─── 音声ファイル読み込み ────────────────────────────
    audio_b64_list = []   # スライドごとの base64 WAV（なければ空文字）
    audio_found = 0
    for seg in preview_segs:
        # audio_files（複数ラン）を結合、なければ audio_file（単体）を使う
        paths = seg.get("audio_files", [])
        if not paths:
            af = seg.get("audio_file", "")
            paths = [af] if af else []
        # 最初の存在するファイルを使う
        b64 = ""
        for p in paths:
            pp = Path(p)
            if pp.exists():
                b64 = "data:audio/wav;base64," + _b64.b64encode(pp.read_bytes()).decode()
                audio_found += 1
                break
        audio_b64_list.append(b64)

    if audio_found == 0:
        st.info("🔇 音声ファイルが見つかりません（VOICEVOX 実行後の JSON を読み込むと音声付きでプレビューできます）")
    else:
        st.caption(f"🔊 {audio_found}/{len(preview_segs)} スライドに音声あり")

    # ─── スライド HTML 生成 ──────────────────────────────
    slides_html = []
    for seg in preview_segs:
        renderer = RENDERERS.get(seg.get("slide_layout", "feature"), _render_feature)
        slides_html.append(renderer(seg))

    durations = [seg.get("duration_ms", 5000) for seg in preview_segs]
    titles    = [f'#{start_idx+i+1} [{seg.get("slide_layout","")}] {seg.get("slide_title","")}'
                 for i, seg in enumerate(preview_segs)]

    js_slides   = json.dumps(slides_html, ensure_ascii=False)
    js_durs     = json.dumps(durations)
    js_titles   = json.dumps(titles, ensure_ascii=False)
    js_audio    = json.dumps(audio_b64_list)

    player_html = f"""
    <html><head>
    <style>
    {PREVIEW_CSS}
    /* Video Preview: .slide をレスポンシブモードに戻す */
    .slide {{ width:100% !important; height:auto !important; aspect-ratio:16/9;
              border-radius:8px; border:1px solid #e0e0e0; }}
    body {{ background: transparent; padding: 8px 0; font-family: 'Noto Sans JP', sans-serif; }}
    .player-wrap {{ max-width: 100%; }}
    .controls {{ display:flex; align-items:center; gap:10px; margin:10px 0; flex-wrap:wrap; }}
    .controls button {{
      border:2px solid #111; background:#fff; border-radius:8px;
      padding:5px 16px; font-size:14px; cursor:pointer; font-weight:700;
    }}
    .controls button:hover {{ background:#FEF3C0; }}
    .controls button.active {{ background:#FBCB3E; }}
    .progress-bar {{ flex:1; min-width:120px; height:6px; background:#E8E8E0;
                     border-radius:4px; cursor:pointer; }}
    .progress-fill {{ height:100%; background:#FBCB3E; border-radius:4px; transition:width 0.15s; }}
    .info {{ font-size:12px; color:#666; margin:2px 0; }}
    </style>
    </head><body>
    <div class="player-wrap">
      <div class="slide" id="stage"></div>
      <div class="controls">
        <button onclick="prev()">⏮</button>
        <button id="btn-play" onclick="toggle()">▶ 再生</button>
        <button onclick="next()">⏭</button>
        <div class="progress-bar" onclick="seek(event)">
          <div class="progress-fill" id="pfill"></div>
        </div>
        <span class="info" id="lbl"></span>
      </div>
      <div class="info" id="detail"></div>
    </div>
    <script>
    const slides = {js_slides};
    const durs   = {js_durs};
    const titles = {js_titles};
    const audioSrcs = {js_audio};
    let idx = 0, playing = false, timer = null;
    let currentAudio = null;
    const stage   = document.getElementById('stage');
    const pfill   = document.getElementById('pfill');
    const lbl     = document.getElementById('lbl');
    const detail  = document.getElementById('detail');
    const btnPlay = document.getElementById('btn-play');

    function stopAudio() {{
      if (currentAudio) {{ currentAudio.pause(); currentAudio = null; }}
    }}
    function playAudio(i) {{
      stopAudio();
      if (audioSrcs[i]) {{
        currentAudio = new Audio(audioSrcs[i]);
        currentAudio.play().catch(()=>{{}});
      }}
    }}

    function show(i, withAudio) {{
      idx = Math.max(0, Math.min(i, slides.length - 1));
      stage.innerHTML = slides[idx];
      pfill.style.width = ((idx + 1) / slides.length * 100) + '%';
      lbl.textContent = (idx + 1) + '/' + slides.length;
      detail.textContent = titles[idx] + '  (' + (durs[idx] / 1000).toFixed(1) + '秒)';
      if (withAudio) playAudio(idx);
    }}
    function next() {{
      if (idx < slides.length - 1) show(idx + 1, playing);
      else {{ playing = false; updateBtn(); stopAudio(); }}
    }}
    function prev() {{ stopAudio(); show(Math.max(0, idx - 1), false); }}
    function toggle() {{
      playing = !playing;
      updateBtn();
      if (playing) {{ playAudio(idx); advance(); }}
      else {{ clearTimeout(timer); stopAudio(); }}
    }}
    function updateBtn() {{
      btnPlay.textContent = playing ? '⏸ 停止' : '▶ 再生';
      btnPlay.className = playing ? 'active' : '';
    }}
    function advance() {{
      if (!playing) return;
      timer = setTimeout(() => {{ next(); if (playing) advance(); }}, durs[idx]);
    }}
    function seek(e) {{
      const rect = e.currentTarget.getBoundingClientRect();
      const pct = (e.clientX - rect.left) / rect.width;
      show(Math.floor(pct * slides.length), false);
    }}
    show(0, false);
    </script>
    </body></html>
    """
    st.components.v1.html(player_html, height=440, scrolling=False)

    # ─── スライド一覧 ────────────────────────────────────
    with st.expander(f"📋 全スライド一覧（{total}件）", expanded=False):
        for i, seg in enumerate(segs_vp):
            dur = seg.get("duration_ms", 0) / 1000
            has_af = "🔊" if seg.get("audio_file") else "🔇"
            marker = " ← ★" if start_idx <= i < start_idx + n_preview else ""
            st.text(f"#{i+1} {has_af} [{seg.get('slide_layout','?')}] "
                    f"{seg.get('slide_title','')[:25]}  ({dur:.1f}秒){marker}")
    st.stop()

# ─── サイドバー ─────────────────────────────────────────
with st.sidebar:
    st.header("📂 ファイル")
    json_path_input = st.text_input("JSONファイルパス", value=str(DEFAULT_JSON))
    uploaded = st.file_uploader("またはアップロード", type="json")
    load_btn = st.button("📥 読み込む", use_container_width=True)

    st.divider()
    st.header("💾 保存")
    save_path = st.text_input("保存先", value=json_path_input)
    col_s1, col_s2 = st.columns(2)
    save_btn = col_s1.button("💾 保存", use_container_width=True, type="primary")
    save_merged_btn = col_s2.button("📦 merged保存", use_container_width=True,
                                     help="segments_merged.json として保存（VOICEVOX入力用）")

    st.divider()
    st.header("🖥️ サーバー状態")

    # Whisper
    whisper_info = _check_server(WHISPER_URL, "/health")
    if whisper_info:
        st.success(f"🎙️ Whisper: ✅ 稼働中 (model: {whisper_info.get('model','?')})")
    else:
        st.error("🎙️ Whisper: ❌ 停止中")
        if st.button("🚀 Whisper 起動", use_container_width=True,
                      help="whisper_server/server.py を起動"):
            try:
                subprocess.Popen(
                    ["python", "whisper_server/server.py"],
                    cwd=str(Path.cwd()),
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
                st.info("起動中... 数秒後にリロードしてください")
            except Exception as e:
                st.error(f"起動失敗: {e}")

    # VOICEVOX
    voicevox_info = _check_server(VOICEVOX_URL, "/version")
    if voicevox_info:
        st.success(f"🔊 VOICEVOX: ✅ v{voicevox_info}")
    else:
        st.error("🔊 VOICEVOX: ❌ 停止中")
        st.caption("VOICEVOX アプリを手動で起動してください")

    st.divider()
    st.header("🔍 フィルター")
    filter_layout = st.selectbox("レイアウト絞り込み", ["すべて"] + LAYOUT_OPTIONS)
    filter_text = st.text_input("テキスト検索")

    st.divider()
    st.header("⚡ パイプライン実行")

    _PIPELINE_MODES = {
        "音声から動画生成":             "--audio",
        "Whisper済みから再開":           "--resume-raw",
        "修正済みテキストから再開":       "--resume",
        "マージ済みから再開":             "--resume-merged",
        "スライド生成済みから再開":       "--resume-slides",
        "VOICEVOX済みから再開（Remotionのみ）": "--resume-audio",
        "テキストファイルから新規スタート": "--text",
    }
    _PIPELINE_LABELS = list(_PIPELINE_MODES.keys())

    pipeline_mode = st.selectbox("実行モード", _PIPELINE_LABELS, key="pipe_mode")
    pipeline_opt  = _PIPELINE_MODES[pipeline_mode]

    pipe_skip_correction = False
    if pipeline_opt == "--audio":
        pipe_input = st.text_input("音声ファイルパス", value="", key="pipe_audio")
    elif pipeline_opt == "--resume-raw":
        pipe_input = st.text_input("segments_raw.json パス",
                                    value="output/segments_raw.json", key="pipe_raw")
    elif pipeline_opt == "--resume":
        pipe_input = st.text_input("segments_corrected.json パス",
                                    value="output/segments_corrected.json", key="pipe_corr")
    elif pipeline_opt == "--resume-merged":
        pipe_input = st.text_input("segments_merged.json パス",
                                    value="output/segments_merged.json", key="pipe_merged")
    elif pipeline_opt == "--resume-slides":
        pipe_input = st.text_input("segments_with_slides.json パス",
                                    value="output/segments_with_slides.json", key="pipe_slides")
    elif pipeline_opt == "--resume-audio":
        pipe_input = st.text_input("audio_segments.json パス",
                                    value="output/audio_segments.json", key="pipe_audio_segs")
    else:  # --text
        pipe_input = st.text_input("テキストファイルパス", value="", key="pipe_text")
        pipe_skip_correction = st.checkbox("STEP2（テキスト修正）をスキップ",
                                            key="pipe_skip_corr")

    pipe_preview = st.checkbox(
        "🎬 プレビューモード(低解像度・高速)",
        value=False,
        key="pipe_preview",
        help="960x540 でレンダリング。確認用に数分で完成。",
    )

    if st.button("▶️ パイプライン実行", use_container_width=True,
                  disabled=not pipe_input.strip()):
        cmd = ["python", "pipeline.py", pipeline_opt, pipe_input]
        if pipeline_opt == "--text" and pipe_skip_correction:
            cmd.append("--skip-correction")
        if pipe_preview:
            cmd.append("--preview")
        st.info(f"実行中: {' '.join(cmd)}")
        # Windows(cp932) でも絵文字や日本語を受け取れるように UTF-8 で読む
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        st.code(result.stdout[-3000:] if result.stdout else "")
        if result.returncode == 0:
            st.success("完了！ 読み込みボタンで結果を確認")
        else:
            st.error(f"エラー (code={result.returncode})")
            if result.stderr:
                st.code(result.stderr[-1500:])


# ─── 読み込み ────────────────────────────────────────────
def load_json(data):
    segs = json.loads(data) if isinstance(data, (str, bytes)) else data
    for s in segs:
        s.setdefault("slide_layout",  "feature")
        s.setdefault("slide_title",   s.get("text", "")[:20])
        s.setdefault("slide_sub",     "")
        s.setdefault("slide_items",   [])
        s.setdefault("slide_icon",    "")
        s.setdefault("slide_number",  "")
        s.setdefault("font_scale",    1.0)
    return segs

if load_btn or (uploaded and not st.session_state.loaded):
    try:
        if uploaded:
            st.session_state.segments = load_json(uploaded.read())
        else:
            p = Path(json_path_input)
            if p.exists():
                st.session_state.segments = load_json(p.read_text(encoding="utf-8"))
            else:
                st.error(f"ファイルが見つかりません: {p}")
        st.session_state.loaded = True
        st.toast(f"✅ {len(st.session_state.segments)} セグメント読み込み完了")
    except Exception as e:
        st.error(f"読み込みエラー: {e}")

# ─── 保存 ────────────────────────────────────────────────
if save_btn and st.session_state.segments:
    try:
        out = Path(save_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(st.session_state.segments, f, ensure_ascii=False, indent=2)
        st.sidebar.success(f"✅ 保存: {out}")
    except Exception as e:
        st.sidebar.error(f"保存エラー: {e}")

if save_merged_btn and st.session_state.segments:
    try:
        out = Path("output/segments_merged.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(st.session_state.segments, f, ensure_ascii=False, indent=2)
        st.sidebar.success(f"✅ 保存: {out}")
    except Exception as e:
        st.sidebar.error(f"保存エラー: {e}")

# ═══════════════════════════════════════════════════════
#  Template Editor モード
# ═══════════════════════════════════════════════════════
if mode == "Template Editor":
    st.title("🎛️ Template Editor")
    st.caption("レイアウトごとのデザイン、VOICEVOX、動画出力の設定を管理します。")

    _OVERRIDES_FILE = OVERRIDES_FILE

    # プライマリボタンを黄色アクセントに（タブ・保存ボタン共通）
    st.markdown("""
    <style>
    div[data-testid="stButton"] > button[kind="primary"] {
        background-color: #FBCB3E !important;
        color: #111 !important;
        border-color: #111 !important;
        font-weight: 700 !important;
    }
    </style>
    """, unsafe_allow_html=True)

    # ── VOICEVOX キャラクター名一覧 ──────────────────────
    _SPEAKER_FALLBACK = [
        (0,  "四国めたん（あまあま）"),   (1,  "ずんだもん（あまあま）"),
        (2,  "四国めたん（ノーマル）"),   (3,  "ずんだもん（ノーマル）"),
        (4,  "四国めたん（セクシー）"),   (5,  "ずんだもん（セクシー）"),
        (6,  "四国めたん（ツンツン）"),   (7,  "ずんだもん（ツンツン）"),
        (8,  "春日部つむぎ"),           (10, "波音リツ"),
        (11, "玄野武宏（ノーマル）"),    (13, "青山龍星"),
        (14, "冥鳴ひまり"),             (16, "九州そら（ノーマル）"),
        (20, "もち子さん"),             (21, "剣崎雌雄"),
        (23, "WhiteCUL（ノーマル）"),    (27, "ナースロボ＿タイプＴ（ノーマル）"),
        (42, "ちび式じい"),             (43, "櫻歌ミコ（ノーマル）"),
        (46, "小夜/SAYO"),             (47, "ナースロボ＿タイプＴ（楽々）"),
        (51, "No.7（ノーマル）"),
    ]

    @st.cache_data(ttl=300)
    def _fetch_voicevox_speakers() -> list[tuple[int, str]]:
        try:
            r = requests.get(f"{VOICEVOX_URL}/speakers", timeout=3)
            r.raise_for_status()
            result = []
            for speaker in r.json():
                name = speaker["name"]
                for style in speaker.get("styles", []):
                    sid = style["id"]
                    sname = style.get("name", "")
                    label = f"{name}（{sname}）" if sname and sname != "ノーマル" else name
                    if sname == "ノーマル":
                        label = f"{name}（ノーマル）"
                    result.append((sid, label))
            return sorted(result, key=lambda x: x[0]) if result else _SPEAKER_FALLBACK
        except Exception:
            return _SPEAKER_FALLBACK

    speaker_list = _fetch_voicevox_speakers()
    speaker_ids    = [s[0] for s in speaker_list]
    speaker_labels = [f"{s[1]}  [ID:{s[0]}]" for s in speaker_list]

    def _speaker_index(sid: int) -> int:
        return speaker_ids.index(sid) if sid in speaker_ids else 0

    def _current_cfg() -> dict:
        defaults = {
            "VOICEVOX_SPEAKER_ID": 3,
            "VOICEVOX_SPEED": 1.15, "VOICEVOX_INTONATION": 1.4,
            "VOICEVOX_PITCH": 0.0, "VOICEVOX_VOLUME": 1.0,
            "VOICEVOX_SPEAKERS": {"A": 3, "B": 2, "unknown": 3},
            "VOICEVOX_SPEAKER_PARAMS": {
                "A": {"speedScale": 1.15, "intonationScale": 1.3, "pitchScale": 0.0},
                "B": {"speedScale": 1.10, "intonationScale": 1.5, "pitchScale": 0.02},
            },
            "VIDEO_FPS": 30, "VIDEO_WIDTH": 1920, "VIDEO_HEIGHT": 1080,
            "BACKGROUND_COLOR":     "#FFFFFF",
            "TEXT_COLOR_PRIMARY":   "#111111",
            "TEXT_COLOR_SUB":       "#888888",
            "TEXT_COLOR_ACCENT":    "#FBCB3E",
            # 互換: 旧キー
            "FONT_COLOR": "#000000", "HIGHLIGHT_COLOR": "#000000", "FONT_SIZE": 64,
            "TITLE_SCALE":           1.0,
            "LAYOUT_SUB_SCALES":     {k: 1.0 for k in LAYOUT_KEYS},
            "LAYOUT_ITEM_SCALES":    {k: 1.0 for k in LAYOUT_KEYS},
            "LAYOUT_PADDING_SCALES": {k: 1.0 for k in LAYOUT_KEYS},
            "SHOW_TITLE_BRAND":      False,
            "BRAND_TEXT":            "NotebookLM",
            "SHOW_SUBTITLE": False,
        }
        if _OVERRIDES_FILE.exists():
            try:
                overrides = json.loads(_OVERRIDES_FILE.read_text(encoding="utf-8"))
                # 互換性: 旧 LAYOUT_FONT_SCALES / LAYOUT_TITLE_SCALES はファイルに残すが挙動には反映しない
                overrides.pop("LAYOUT_FONT_SCALES", None)
                # マージ（dict はキー単位で上書き）
                for k, v in overrides.items():
                    if isinstance(defaults.get(k), dict) and isinstance(v, dict):
                        defaults[k] = {**defaults[k], **v}
                    else:
                        defaults[k] = v
            except Exception:
                pass
        return defaults

    cfg = _current_cfg()

    def _save_overrides(updates: dict) -> bool:
        """既存ファイルを読み込んで updates をマージし書き戻す（旧キーは保持）"""
        existing = {}
        if _OVERRIDES_FILE.exists():
            try:
                existing = json.loads(_OVERRIDES_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        merged = {**existing, **updates}
        try:
            _OVERRIDES_FILE.write_text(
                json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return True
        except Exception as e:
            st.error(f"保存エラー: {e}")
            return False

    # ── レイアウトごとの部位定義 ─────────────────────────
    _HAS_SUB   = {"title", "question", "section", "feature"}
    _HAS_ITEMS = {"question", "feature", "split", "flow", "timeline", "bullets", "cards"}

    _DUMMY_DEFAULTS: dict[str, dict] = {
        "title":    {"title": "動画タイトルのサンプル", "sub": "サブテキストの表示例", "items": [], "icon": ""},
        "question": {"title": "なぜキャッシュフローが重要？", "sub": "企業の健全性を測る指標", "items": ["営業CF", "投資CF", "財務CF"], "icon": "？"},
        "section":  {"title": "第1章 基礎知識", "sub": "まずは基本から", "items": [], "icon": ""},
        "feature":  {"title": "キャッシュフローとは", "sub": "企業の現金の流れを示す指標", "items": ["営業活動", "投資活動"], "icon": "💡"},
        "split":    {"title": "利益と現金の違い", "sub": "", "items": ["利益：帳簿上の計算", "現金：実際の支払い能力"], "icon": "⚖️"},
        "flow":     {"title": "分析の3ステップ", "sub": "", "items": ["データ収集", "分析・比較", "レポート作成"], "icon": "🔄"},
        "timeline": {"title": "会計基準の変遷", "sub": "", "items": ["2019年: 開始", "2025年: 移行", "2027年: 完全適用"], "icon": "📅"},
        "bullets":  {"title": "今日のポイント", "sub": "", "items": ["収益認識の基本", "5つのステップ", "実務での注意点"], "icon": "📝"},
        "cards":    {"title": "3つのCF分類", "sub": "",
                     "items": ["営業活動CF: 本業で得たお金の流れ",
                               "投資活動CF: 設備投資の流れ",
                               "財務活動CF: 資金調達の流れ"], "icon": "🗂️"},
    }

    # レイアウトタブ用のラベル（絵文字＋キー）
    _LAYOUT_TAB_LABEL = {k: f"{info[1].split()[0]} {k}" for k, info in zip(LAYOUT_KEYS, LAYOUTS)}

    # ── セクション切替 ───────────────────────────────────
    SECTIONS = ["🎨 レイアウト", "🔊 VOICEVOX", "🎬 動画出力"]
    tpl_section = st.radio("セクション", SECTIONS, horizontal=True,
                            key="tpl_section_sel", label_visibility="collapsed")

    # ═════════════════════════════════════════════════
    #  レイアウト編集
    # ═════════════════════════════════════════════════
    if tpl_section == "🎨 レイアウト":
        # ── 全体共通設定 ─────────────────────────────────
        with st.expander("🌐 全体共通設定（タイトル倍率・色・ブランド）", expanded=True):
            gts_key = "_tpl_global_title_scale"
            if gts_key not in st.session_state:
                st.session_state[gts_key] = float(cfg.get("TITLE_SCALE", 1.0))
            gc1, gc2 = st.columns([2, 1])
            gc1.slider("タイトル倍率（全レイアウト共通）", 0.5, 2.0, step=0.05, key=gts_key)
            gc2.metric("現在値", f"{st.session_state[gts_key]:.2f}×")

            cc1, cc2, cc3, cc4 = st.columns(4)
            tp_key, tsub_key, tac_key, bg_key = (
                "_tpl_color_primary", "_tpl_color_sub",
                "_tpl_color_accent",  "_tpl_color_bg",
            )
            if tp_key not in st.session_state:
                st.session_state[tp_key] = cfg.get("TEXT_COLOR_PRIMARY", "#111111")
            if tsub_key not in st.session_state:
                st.session_state[tsub_key] = cfg.get("TEXT_COLOR_SUB", "#888888")
            if tac_key not in st.session_state:
                st.session_state[tac_key] = cfg.get("TEXT_COLOR_ACCENT", "#FBCB3E")
            if bg_key not in st.session_state:
                st.session_state[bg_key] = cfg.get("BACKGROUND_COLOR", "#FFFFFF")
            cc1.color_picker("本文カラー", key=tp_key)
            cc2.color_picker("サブカラー", key=tsub_key)
            cc3.color_picker("アクセント", key=tac_key)
            cc4.color_picker("背景",       key=bg_key)

            br1, br2 = st.columns([1, 2])
            sb_key, bt_key = "_tpl_show_brand", "_tpl_brand_text"
            if sb_key not in st.session_state:
                st.session_state[sb_key] = bool(cfg.get("SHOW_TITLE_BRAND", False))
            if bt_key not in st.session_state:
                st.session_state[bt_key] = cfg.get("BRAND_TEXT", "NotebookLM")
            br1.checkbox("Title スライドにブランド名を表示", key=sb_key)
            br2.text_input("ブランド名", key=bt_key,
                            disabled=not st.session_state[sb_key])

            if st.button("💾 全体共通設定を保存", use_container_width=True, type="primary",
                         key="save_global_settings"):
                ok = _save_overrides({
                    "TITLE_SCALE":         float(st.session_state[gts_key]),
                    "TEXT_COLOR_PRIMARY":  st.session_state[tp_key],
                    "TEXT_COLOR_SUB":      st.session_state[tsub_key],
                    "TEXT_COLOR_ACCENT":   st.session_state[tac_key],
                    "BACKGROUND_COLOR":    st.session_state[bg_key],
                    "SHOW_TITLE_BRAND":    bool(st.session_state[sb_key]),
                    "BRAND_TEXT":          st.session_state[bt_key],
                })
                if ok:
                    st.success("✅ 全体共通設定を保存しました（全プレビューに即反映）")
                    st.rerun()

        st.divider()

        # レイアウトタブ
        tab_cols = st.columns(len(LAYOUTS))
        if "tpl_layout" not in st.session_state:
            st.session_state["tpl_layout"] = "feature"
        for i, k in enumerate(LAYOUT_KEYS):
            is_active = (k == st.session_state["tpl_layout"])
            if tab_cols[i].button(
                _LAYOUT_TAB_LABEL[k], key=f"tpl_tab_{k}",
                type="primary" if is_active else "secondary",
                use_container_width=True,
            ):
                st.session_state["tpl_layout"] = k
                st.rerun()

        layout_key = st.session_state["tpl_layout"]
        defaults = _DUMMY_DEFAULTS.get(layout_key,
                                        {"title": "サンプル", "sub": "", "items": [], "icon": ""})

        # 調整対象トグルの選択肢を決定（タイトルは全体共通へ移動）
        target_options = ["余白"]
        if layout_key in _HAS_SUB:
            target_options.append("サブ")
        if layout_key in _HAS_ITEMS:
            target_options.append("項目")

        # スケール値をセッションに初期化（保存済みの値を優先）
        cfg_sub_scales   = cfg.get("LAYOUT_SUB_SCALES",     {})
        cfg_item_scales  = cfg.get("LAYOUT_ITEM_SCALES",    {})
        cfg_pad_scales   = cfg.get("LAYOUT_PADDING_SCALES", {})
        ss_key  = f"_tpl_ss_{layout_key}"
        is_key  = f"_tpl_is_{layout_key}"
        pad_key = f"_tpl_pad_{layout_key}"
        if ss_key not in st.session_state:
            st.session_state[ss_key]  = float(cfg_sub_scales.get(layout_key, 1.0))
        if is_key not in st.session_state:
            st.session_state[is_key]  = float(cfg_item_scales.get(layout_key, 1.0))
        if pad_key not in st.session_state:
            st.session_state[pad_key] = float(cfg_pad_scales.get(layout_key, 1.0))

        pv_col, ed_col = st.columns([1.7, 1])

        # ── 右：調整パネル ──
        with ed_col:
            target = st.radio("調整対象", target_options, horizontal=True,
                               key=f"_tpl_target_{layout_key}")
            if target == "余白":
                st.slider("余白倍率", 0.0, 2.0, step=0.05, key=pad_key,
                          help="0で余白なし、1で標準、2で倍")
            elif target == "サブ":
                st.slider("サブ倍率", 0.5, 2.0, step=0.05, key=ss_key)
            elif target == "項目":
                st.slider("項目倍率", 0.5, 2.0, step=0.05, key=is_key)

            st.caption(f"タイトル倍率は「全体共通設定」で一括管理（現在 {float(st.session_state.get(gts_key, 1.0)):.2f}×）")

            st.markdown("**ダミーテキスト**")
            dummy_title = st.text_input("タイトル", value=defaults["title"],
                                         key=f"tpl_ti_{layout_key}")
            dummy_sub = ""
            if layout_key in _HAS_SUB:
                dummy_sub = st.text_input("サブ", value=defaults["sub"],
                                           key=f"tpl_su_{layout_key}")
            dummy_items: list[str] = []
            if layout_key in _HAS_ITEMS:
                max_items_map = {"cards": 6, "question": 4, "flow": 3, "bullets": 4,
                                 "split": 2, "timeline": 4, "feature": 3}
                max_items = max_items_map.get(layout_key, 0)
                for j in range(max_items):
                    default_val = defaults["items"][j] if j < len(defaults["items"]) else ""
                    val = st.text_input(f"項目{j+1}", value=default_val,
                                         key=f"tpl_it_{layout_key}_{j}")
                    if val.strip():
                        dummy_items.append(val.strip())

            st.markdown("")
            if st.button("💾 このレイアウトの設定を保存", use_container_width=True, type="primary",
                         key=f"save_layout_{layout_key}"):
                sub_scales  = dict(cfg.get("LAYOUT_SUB_SCALES",     {}))
                item_scales = dict(cfg.get("LAYOUT_ITEM_SCALES",    {}))
                pad_scales  = dict(cfg.get("LAYOUT_PADDING_SCALES", {}))
                sub_scales[layout_key]  = float(st.session_state[ss_key])
                item_scales[layout_key] = float(st.session_state[is_key])
                pad_scales[layout_key]  = float(st.session_state[pad_key])
                ok = _save_overrides({
                    "LAYOUT_SUB_SCALES":     sub_scales,
                    "LAYOUT_ITEM_SCALES":    item_scales,
                    "LAYOUT_PADDING_SCALES": pad_scales,
                })
                if ok:
                    st.success(f"✅ {layout_key} の設定を保存しました")
                    st.rerun()

        # ── 左：大きなプレビュー ──
        with pv_col:
            preview_seg = {
                "slide_layout": layout_key,
                "slide_title":  dummy_title,
                "slide_sub":    dummy_sub,
                "slide_items":  dummy_items,
                "slide_icon":   defaults.get("icon", ""),
                "slide_number": "01" if layout_key == "section" else "",
                "font_scale":   1.0,
                # ライブプレビュー: スライダー値を渡し、保存前でも反映させる
                "_title_scale": float(st.session_state.get(gts_key, 1.0)),
                "_sub_scale":   float(st.session_state[ss_key]),
                "_item_scale":  float(st.session_state[is_key]),
                "_pad_scale":   float(st.session_state[pad_key]),
            }
            # ライブカラー反映: 一時的に色設定を override したかのように描画
            # （render 関数は _load_view_settings() を呼ぶので、ここはセッション値を渡せない）
            # → ライブ反映のため、一時的に上書き保存はせず、cfgのカラーを viewsettings に注入する
            html = _render_with_overrides(preview_seg, {
                "TEXT_COLOR_PRIMARY":  st.session_state[tp_key],
                "TEXT_COLOR_SUB":      st.session_state[tsub_key],
                "TEXT_COLOR_ACCENT":   st.session_state[tac_key],
                "BACKGROUND_COLOR":    st.session_state[bg_key],
                "SHOW_TITLE_BRAND":    bool(st.session_state[sb_key]),
                "BRAND_TEXT":          st.session_state[bt_key],
            })
            st.components.v1.html(html, height=620, scrolling=False)

    # ═════════════════════════════════════════════════
    #  VOICEVOX 設定
    # ═════════════════════════════════════════════════
    elif tpl_section == "🔊 VOICEVOX":
        st.header("🔊 VOICEVOX 音声")
        cfg_speakers = cfg["VOICEVOX_SPEAKERS"]

        st.markdown("**話者キャラ割り当て**")
        tc1, tc2, tc3 = st.columns(3)
        sel_a = tc1.selectbox("話者A", speaker_labels,
                              index=_speaker_index(int(cfg_speakers.get("A", 3))),
                              key="cfg_spk_a")
        cfg_spk_a = speaker_ids[speaker_labels.index(sel_a)]
        sel_b = tc2.selectbox("話者B", speaker_labels,
                              index=_speaker_index(int(cfg_speakers.get("B", 2))),
                              key="cfg_spk_b")
        cfg_spk_b = speaker_ids[speaker_labels.index(sel_b)]
        sel_def = tc3.selectbox("デフォルト（unknown）", speaker_labels,
                                index=_speaker_index(int(cfg.get("VOICEVOX_SPEAKER_ID", 3))),
                                key="cfg_spk_def")
        cfg_spk_default = speaker_ids[speaker_labels.index(sel_def)]

        st.markdown("**グローバル音声パラメータ**")
        gc1, gc2, gc3, gc4 = st.columns(4)
        cfg_speed = gc1.slider("話速", 0.5, 2.0, float(cfg["VOICEVOX_SPEED"]), 0.05, key="cfg_speed")
        cfg_inton = gc2.slider("抑揚", 0.5, 2.0, float(cfg["VOICEVOX_INTONATION"]), 0.05, key="cfg_inton")
        cfg_pitch = gc3.slider("ピッチ", -0.5, 0.5, float(cfg["VOICEVOX_PITCH"]), 0.01, key="cfg_pitch")
        cfg_vol   = gc4.slider("音量", 0.5, 2.0, float(cfg["VOICEVOX_VOLUME"]), 0.05, key="cfg_vol")

        ac1, ac2 = st.columns(2)
        with ac1:
            st.markdown("**話者A 個別設定**")
            sp_a = cfg.get("VOICEVOX_SPEAKER_PARAMS", {}).get("A", {})
            cfg_a_speed = st.slider("A: 話速", 0.5, 2.0, float(sp_a.get("speedScale", cfg_speed)), 0.05, key="cfg_a_spd")
            cfg_a_inton = st.slider("A: 抑揚", 0.5, 2.0, float(sp_a.get("intonationScale", cfg_inton)), 0.05, key="cfg_a_int")
            cfg_a_pitch = st.slider("A: ピッチ", -0.5, 0.5, float(sp_a.get("pitchScale", 0.0)), 0.01, key="cfg_a_pit")
        with ac2:
            st.markdown("**話者B 個別設定**")
            sp_b = cfg.get("VOICEVOX_SPEAKER_PARAMS", {}).get("B", {})
            cfg_b_speed = st.slider("B: 話速", 0.5, 2.0, float(sp_b.get("speedScale", cfg_speed)), 0.05, key="cfg_b_spd")
            cfg_b_inton = st.slider("B: 抑揚", 0.5, 2.0, float(sp_b.get("intonationScale", cfg_inton)), 0.05, key="cfg_b_int")
            cfg_b_pitch = st.slider("B: ピッチ", -0.5, 0.5, float(sp_b.get("pitchScale", 0.0)), 0.01, key="cfg_b_pit")

        st.divider()
        if st.button("💾 VOICEVOX 設定を保存", use_container_width=True,
                     type="primary", key="save_voicevox"):
            ok = _save_overrides({
                "VOICEVOX_SPEAKER_ID": cfg_spk_default,
                "VOICEVOX_SPEAKERS": {
                    "A": cfg_spk_a, "B": cfg_spk_b, "unknown": cfg_spk_default,
                },
                "VOICEVOX_SPEED": cfg_speed,
                "VOICEVOX_INTONATION": cfg_inton,
                "VOICEVOX_PITCH": cfg_pitch,
                "VOICEVOX_VOLUME": cfg_vol,
                "VOICEVOX_SPEAKER_PARAMS": {
                    "A": {"speedScale": cfg_a_speed, "intonationScale": cfg_a_inton,
                          "pitchScale": cfg_a_pitch},
                    "B": {"speedScale": cfg_b_speed, "intonationScale": cfg_b_inton,
                          "pitchScale": cfg_b_pitch},
                },
            })
            if ok:
                st.success("✅ VOICEVOX 設定を保存しました")

    # ═════════════════════════════════════════════════
    #  動画出力設定
    # ═════════════════════════════════════════════════
    elif tpl_section == "🎬 動画出力":
        st.header("🎬 動画出力")
        st.caption("色やタイトル倍率は『🎨 レイアウト』タブの全体共通設定で変更できます。")
        vc1, vc2, vc3 = st.columns(3)
        cfg_fps = vc1.selectbox("FPS", [24, 30, 60],
                                 index=[24,30,60].index(cfg["VIDEO_FPS"]), key="cfg_fps")
        cfg_w = vc2.number_input("幅", 640, 3840, cfg["VIDEO_WIDTH"], 160, key="cfg_w")
        cfg_h = vc3.number_input("高さ", 360, 2160, cfg["VIDEO_HEIGHT"], 90, key="cfg_h")

        cfg_show_sub = st.checkbox("字幕表示（将来実装予定）",
                                    value=cfg.get("SHOW_SUBTITLE", False),
                                    key="cfg_show_sub")
        if cfg_show_sub:
            cfg_fontsize = st.slider("字幕フォントサイズ", 24, 120,
                                      int(cfg["FONT_SIZE"]), 4, key="cfg_fs")
        else:
            cfg_fontsize = int(cfg.get("FONT_SIZE", 64))

        st.divider()
        if st.button("💾 動画出力設定を保存", use_container_width=True,
                     type="primary", key="save_video"):
            ok = _save_overrides({
                "VIDEO_FPS": cfg_fps,
                "VIDEO_WIDTH": cfg_w,
                "VIDEO_HEIGHT": cfg_h,
                "FONT_SIZE": cfg_fontsize,
                "SHOW_SUBTITLE": cfg_show_sub,
            })
            if ok:
                st.success("✅ 動画出力設定を保存しました")

    st.stop()

# ─── メイン ─────────────────────────────────────────────
st.title("🎨 スライドエディタ v2")
st.caption("プレビューを見ながらスライドを編集。変更は即座にプレビューに反映されます。")

if not st.session_state.segments:
    st.info("👈 サイドバーからファイルを読み込んでください。")
    st.stop()

segs = st.session_state.segments

# ─── フィルタリング ──────────────────────────────────────
filtered = []
for i, seg in enumerate(segs):
    if filter_layout != "すべて":
        selected_key = LAYOUT_KEYS[LAYOUT_OPTIONS.index(filter_layout)]
        if seg["slide_layout"] != selected_key:
            continue
    if filter_text:
        if (filter_text not in seg.get("text", "")
                and filter_text not in seg.get("slide_title", "")):
            continue
    filtered.append(i)

# ─── 統計 ────────────────────────────────────────────────
mc1, mc2, mc3, mc4 = st.columns(4)
mc1.metric("総スライド数", len(segs))
mc2.metric("表示中", len(filtered))

layout_counts = {}
for s in segs:
    k = s["slide_layout"]
    layout_counts[k] = layout_counts.get(k, 0) + 1
most_used = max(layout_counts, key=layout_counts.get) if layout_counts else "-"
mc3.metric("最多レイアウト", LAYOUT_LABELS.get(most_used, most_used).split("—")[0].strip())

total_dur = sum(s.get("duration_ms", 0) for s in segs) / 1000
mc4.metric("総尺（秒）", f"{total_dur:.0f}")

st.divider()

# ─── ページング ──────────────────────────────────────────
PAGE_SIZE = 5
total_pages = max(1, (len(filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
nav1, nav2, nav3 = st.columns([1, 3, 1])
page = nav2.number_input("ページ", 1, total_pages, 1, label_visibility="collapsed") - 1
nav1.write(f"📄 {page+1}/{total_pages}")
nav3.write(f"{len(filtered)} 件")

page_indices = filtered[page * PAGE_SIZE: (page + 1) * PAGE_SIZE]

# ─── スライド一覧 ────────────────────────────────────────
for i in page_indices:
    seg = segs[i]
    layout_key = seg["slide_layout"]

    with st.container(border=True):
        # ─── ヘッダー行（番号・レイアウト名・操作ボタン）
        hc1, hc2, hc3_badge, hc4, hc5, hc6 = st.columns([0.6, 1.8, 0.9, 0.5, 0.5, 0.5])
        hc1.markdown(f"### #{i+1}")
        hc2.markdown(f"**[{layout_key}]** {seg.get('slide_title','')[:30]}")
        hc3_badge.markdown(
            _speaker_badge_html(seg.get("speaker", "unknown")),
            unsafe_allow_html=True,
        )

        # 順序入れ替え
        if hc4.button("⬆️", key=f"up_{i}", disabled=(i == 0),
                      help="1つ上に移動"):
            segs[i], segs[i-1] = segs[i-1], segs[i]
            st.session_state.segments = segs
            st.rerun()
        if hc5.button("⬇️", key=f"down_{i}", disabled=(i >= len(segs)-1),
                      help="1つ下に移動"):
            segs[i], segs[i+1] = segs[i+1], segs[i]
            st.session_state.segments = segs
            st.rerun()
        if hc6.button("🗑️", key=f"del_{i}", help="このスライドを削除"):
            segs.pop(i)
            st.session_state.segments = segs
            st.rerun()

        # ─── プレビュー + 編集エリア
        preview_col, edit_col = st.columns([3, 2])

        with preview_col:
            # session_state から最新のウィジェット値でプレビュー用 dict を構築
            preview_seg = dict(seg)
            # ウィジェットの key から最新値を取得（2回目以降のレンダリングで有効）
            for field, key_prefix in [("slide_title", "ti_"), ("slide_sub", "su_"),
                                       ("slide_icon", "ic_"), ("slide_number", "nu_")]:
                sk = f"{key_prefix}{i}"
                if sk in st.session_state:
                    preview_seg[field] = st.session_state[sk]
            ly_key = f"ly_{i}"
            if ly_key in st.session_state:
                selected = st.session_state[ly_key]
                if selected in LAYOUT_OPTIONS:
                    preview_seg["slide_layout"] = LAYOUT_KEYS[LAYOUT_OPTIONS.index(selected)]
            html = render_preview_html(preview_seg)
            st.components.v1.html(html, height=360, scrolling=False)

            preview_speaker = preview_seg.get("speaker", seg.get("speaker", "unknown"))
            preview_badge = _speaker_badge_html(preview_speaker)
            preview_dur = preview_seg.get("duration_ms", 0) / 1000
            st.caption(
                f"話者: {preview_badge}　|　尺: {preview_dur:.1f}秒 "
                f"　|　マージ数: {preview_seg.get('merged_count', 1)}",
                unsafe_allow_html=True,
            )

            # 元テキスト編集
            with st.expander("📝 元のテキスト", expanded=False):
                runs_text = _format_speaker_runs(seg)
                if runs_text:
                    st.markdown(runs_text, unsafe_allow_html=True)
                new_text = st.text_area(
                    "テキスト", value=seg.get("text", ""),
                    key=f"txt_{i}", height=100, label_visibility="collapsed",
                )
                if segs[i].get("text", "") != new_text:
                    segs[i]["text"] = new_text
                    st.session_state.segments = segs
                dur = seg.get("duration_ms", 0) / 1000
                badge = _speaker_badge_html(seg.get("speaker", "unknown"))
                st.caption(
                    f"話者: {badge}　|　尺: {dur:.1f}秒　|　マージ数: {seg.get('merged_count', 1)}",
                    unsafe_allow_html=True,
                )

        with edit_col:
            # クイックレイアウト切り替え（ワンクリック → 即プレビュー更新）
            QUICK = [("📌","title"),("❓","question"),("🟡","section"),("💡","feature"),
                     ("⚖️","split"),("➡️","flow"),("📅","timeline"),("📋","bullets"),("🗂️","cards")]
            qcols = st.columns(len(QUICK))
            for qi, (emoji, lk) in enumerate(QUICK):
                is_active = (layout_key == lk)
                if qcols[qi].button(
                    emoji, key=f"q_{i}_{lk}",
                    help=lk,
                    type="primary" if is_active else "secondary",
                    use_container_width=True,
                ):
                    if not is_active:
                        segs[i]["slide_layout"] = lk
                        st.session_state.segments = segs
                        st.rerun()

            # レイアウト選択（ドロップダウン）
            current_idx = LAYOUT_KEYS.index(layout_key) if layout_key in LAYOUT_KEYS else 3
            new_layout_option = st.selectbox(
                "レイアウト", LAYOUT_OPTIONS, index=current_idx, key=f"ly_{i}",
                label_visibility="collapsed",
            )
            new_layout = LAYOUT_KEYS[LAYOUT_OPTIONS.index(new_layout_option)]

            # タイトル
            new_title = st.text_input("タイトル（20字）", value=seg.get("slide_title", ""),
                                       key=f"ti_{i}", max_chars=20)
            # サブテキスト
            new_sub = st.text_input("サブテキスト（30字）", value=seg.get("slide_sub", ""),
                                     key=f"su_{i}", max_chars=30)

            ic1, ic2 = st.columns(2)
            new_icon = ic1.text_input("絵文字", value=seg.get("slide_icon", ""), key=f"ic_{i}")
            new_number = ic2.text_input("番号", value=seg.get("slide_number", ""), key=f"nu_{i}")

            # アイテムリスト（タグ・箇条書き）
            needs_items = new_layout in ("question", "feature", "flow", "bullets", "split", "cards", "timeline")
            if needs_items:
                st.markdown("**リスト項目**")
                items = seg.get("slide_items", [])
                max_items = 6 if new_layout == "cards" else 4
                new_items = []
                item_cols = st.columns(2)
                for j in range(max_items):
                    val = items[j] if j < len(items) else ""
                    nv = item_cols[j % 2].text_input(
                        f"項目{j+1}", value=val, key=f"it_{i}_{j}", max_chars=30
                    )
                    if nv.strip():
                        new_items.append(nv.strip())
            else:
                new_items = seg.get("slide_items", [])

            # 自動反映（フィールド変更時に即座にデータ更新）
            changed = False
            if segs[i]["slide_layout"] != new_layout:
                segs[i]["slide_layout"] = new_layout
                changed = True
            if segs[i].get("slide_title", "") != new_title:
                segs[i]["slide_title"] = new_title
                changed = True
            if segs[i].get("slide_sub", "") != new_sub:
                segs[i]["slide_sub"] = new_sub
                changed = True
            if segs[i].get("slide_icon", "") != new_icon:
                segs[i]["slide_icon"] = new_icon
                changed = True
            if segs[i].get("slide_number", "") != new_number:
                segs[i]["slide_number"] = new_number
                changed = True
            if segs[i].get("slide_items", []) != new_items:
                segs[i]["slide_items"] = new_items
                changed = True
            if changed:
                st.session_state.segments = segs

# ─── 一括操作 ──────────────────────────────────────────
st.divider()
st.subheader("⚡ 一括操作")
bc1, bc2, bc3 = st.columns(3)

with bc1:
    st.markdown("**レイアウト一括変換**")
    bulk_from = st.selectbox("変更前", LAYOUT_OPTIONS, key="bf")
    bulk_to   = st.selectbox("変更後", LAYOUT_OPTIONS, key="bt")
    if st.button("一括変換", use_container_width=True):
        fk = LAYOUT_KEYS[LAYOUT_OPTIONS.index(bulk_from)]
        tk = LAYOUT_KEYS[LAYOUT_OPTIONS.index(bulk_to)]
        cnt = sum(1 for s in segs if s["slide_layout"] == fk)
        for s in segs:
            if s["slide_layout"] == fk:
                s["slide_layout"] = tk
        st.session_state.segments = segs
        st.toast(f"✅ {cnt} 件を {fk} → {tk} に変換")
        st.rerun()

with bc2:
    st.markdown("**section 連番リセット**")
    if st.button("🔢 連番を振り直す", use_container_width=True):
        n = 0
        for s in segs:
            if s["slide_layout"] == "section":
                n += 1
                s["slide_number"] = str(n).zfill(2)
        st.session_state.segments = segs
        st.toast(f"✅ {n} 件のsectionに連番付与")
        st.rerun()

    st.markdown("**新しいスライドを追加**")
    add_pos = st.number_input("挿入位置", 1, max(len(segs), 1), len(segs), key="addpos")
    if st.button("➕ 空スライドを挿入", use_container_width=True):
        new_seg = {
            "text": "", "speaker": "unknown", "start": 0, "end": 0,
            "duration_ms": 5000, "index": add_pos,
            "slide_layout": "feature", "slide_title": "新しいスライド",
            "slide_sub": "", "slide_items": [], "slide_icon": "✨",
            "slide_number": "",
        }
        segs.insert(add_pos - 1, new_seg)
        st.session_state.segments = segs
        st.toast(f"✅ #{add_pos} に空スライドを挿入")
        st.rerun()

with bc3:
    st.markdown("**レイアウト分布**")
    for k, cnt in sorted(layout_counts.items(), key=lambda x: -x[1]):
        label = LAYOUT_LABELS.get(k, k).split("—")[0].strip()
        st.progress(cnt / max(len(segs), 1), text=f"{label}: {cnt}件")
