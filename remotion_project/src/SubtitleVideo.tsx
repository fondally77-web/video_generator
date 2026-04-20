/**
 * SubtitleVideo.tsx — v3.1
 * ・マージされた複数 audio_files を1スライドで連続再生
 * ・テキストはスライドタイトル/サブ/アイテムのみ表示（原文は使わない）
 * ・フォントサイズを文字数に応じて自動調整してオーバーフロー防止
 */
import React from "react";
import {
  AbsoluteFill, Audio, Sequence,
  useCurrentFrame, interpolate, spring,
} from "remotion";

// ─── 型 ───────────────────────────────────────────────
interface Seg {
  index: number; start_ms: number; duration_ms: number;
  text: string; speaker: string;
  audio_file: string;
  run_durations_ms: number[];   // 各音声ランの再生時間（ms）
  audio_files?: string[];       // マージされた複数ファイル
  slide_layout: string;
  slide_title: string;
  slide_sub: string;
  slide_items: string[];
  slide_icon: string;
  slide_number: string;
}
interface Props {
  fps: number; durationInFrames: number; width: number; height: number;
  originalAudio: string; segments: Seg[];
  backgroundColor?: string; fontColor?: string;
  highlightColor?: string; fontSize?: number;
  layoutTitleScales?: Record<string, number>;
  layoutSubScales?:   Record<string, number>;
  layoutItemScales?:  Record<string, number>;
}
export const defaultProps: Props = {
  fps: 30, durationInFrames: 300, width: 1920, height: 1080,
  originalAudio: "", segments: [],
};

// ─── デザイン定数 ──────────────────────────────────────
const W   = "#FFFFFF";
const B   = "#111111";
const Y   = "#FBCB3E";
const YL  = "#FEF3C0";
const MG  = "#888888";
const LG  = "#CCCCCC";
const F   = "'Noto Sans JP','Hiragino Kaku Gothic ProN','Yu Gothic',sans-serif";
const cl  = { extrapolateLeft: "clamp" as const, extrapolateRight: "clamp" as const };

// ─── ユーティリティ ────────────────────────────────────
const spr = (f: number, fps: number, from: number, to = 0, delay = 0) =>
  spring({ frame: Math.max(f - delay, 0), fps, config: { stiffness: 200, damping: 18 }, from, to });

const fadeIn = (f: number, dur: number) => Math.min(
  interpolate(f, [0, 10], [0, 1], cl),
  interpolate(f, [dur - 8, dur], [1, 0], cl)
);

/** テキスト長に応じてフォントサイズを自動調整 */
const autoFs = (text: string, max = 80, min = 28) => {
  const len = text.length;
  const fs  = len <= 8 ? max : len <= 14 ? max * 0.85 : len <= 20 ? max * 0.7 : max * 0.58;
  return Math.max(min, Math.round(fs));
};

/** autoFs にスケール倍率を掛ける */
const scaledAutoFs = (text: string, max: number, min: number, scale = 1) =>
  autoFs(text, max * scale, min * scale);

/** レイアウトコンポーネント共通 props */
interface LayoutProps {
  seg: Seg; rf: number; dur: number; fps: number;
  ts?: number;   // title scale
  ss?: number;   // sub scale
  its?: number;  // item scale
}

// ─── 共通部品 ──────────────────────────────────────────

const GridBg: React.FC<{ color?: string }> = ({ color = W }) => (
  <div style={{ position: "absolute", inset: 0, background: color }}>
    <svg style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }} viewBox="0 0 1920 1080">
      {Array.from({ length: 54 }).map((_, i) => (
        <line key={`v${i}`} x1={i * 36} y1="0" x2={i * 36} y2="1080" stroke="#DEDEDE" strokeWidth="0.8" />
      ))}
      {Array.from({ length: 31 }).map((_, i) => (
        <line key={`h${i}`} x1="0" y1={i * 36} x2="1920" y2={i * 36} stroke="#DEDEDE" strokeWidth="0.8" />
      ))}
    </svg>
  </div>
);

const Bar: React.FC<{ rf: number; dur: number }> = ({ rf, dur }) => (
  <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 4, background: "#E8E8E0" }}>
    <div style={{ height: "100%", width: `${Math.min(rf / Math.max(dur, 1), 1) * 100}%`, background: Y }} />
  </div>
);

/** 黄色オフセット影付きカード */
const YCard: React.FC<{
  children: React.ReactNode;
  style?: React.CSSProperties;
  offset?: number; radius?: number; minH?: number;
}> = ({ children, style = {}, offset = 8, radius = 16, minH = 0 }) => (
  <div style={{ position: "relative", ...style }}>
    <div style={{ position: "absolute", inset: 0, background: Y, borderRadius: radius, border: `2.5px solid ${B}`, transform: `translate(${offset}px,${offset}px)` }} />
    <div style={{ position: "relative", background: W, borderRadius: radius, border: `2.5px solid ${B}`, padding: "36px 44px", minHeight: minH }}>
      {children}
    </div>
  </div>
);

/** フェードスライドイン */
const Appear: React.FC<{
  rf: number; fps: number; delay?: number;
  from?: "bottom" | "left" | "right" | "scale";
  children: React.ReactNode;
}> = ({ rf, fps, delay = 0, from = "bottom", children }) => {
  const ty = from === "bottom" ? spr(rf, fps, 40, 0, delay) : 0;
  const tx = from === "left"   ? spr(rf, fps, -60, 0, delay)
           : from === "right"  ? spr(rf, fps, 60, 0, delay) : 0;
  const sc = from === "scale"  ? spr(rf, fps, 0.88, 1, delay, 160, 18) : 1;
  const op = interpolate(rf, [delay, delay + 10], [0, 1], cl);
  return (
    <div style={{ transform: `translate(${tx}px,${ty}px) scale(${sc})`, opacity: op }}>
      {children}
    </div>
  );
};

// ─── レイアウト群 ──────────────────────────────────────

const LayoutTitle: React.FC<LayoutProps> = ({ seg, rf, dur, fps, ts = 1, ss = 1 }) => {
  const op = fadeIn(rf, dur);
  const lw = interpolate(rf, [5, 25], [0, 280], cl);
  const fs = scaledAutoFs(seg.slide_title, 88, 48, ts);
  return (
    <AbsoluteFill style={{ background: W }}>
      <GridBg />
      <AbsoluteFill style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", opacity: op }}>
        <Appear rf={rf} fps={fps} from="scale">
          <div style={{ fontFamily: F, fontSize: 22, fontWeight: 400, color: MG, letterSpacing: "0.2em", marginBottom: 20, textAlign: "center" }}>NotebookLM</div>
          <div style={{ width: lw, height: 2, background: B, margin: "0 auto 32px", opacity: 0.5 }} />
          {seg.slide_icon && <div style={{ fontSize: 56, textAlign: "center", marginBottom: 12 }}>{seg.slide_icon}</div>}
          <div style={{ fontFamily: F, fontSize: fs, fontWeight: 900, color: B, textAlign: "center", lineHeight: 1.3 }}>
            {seg.slide_title}
          </div>
          {seg.slide_sub && <div style={{ fontFamily: F, fontSize: Math.round(36 * ss), color: MG, textAlign: "center", marginTop: 24 }}>{seg.slide_sub}</div>}
          <div style={{ width: lw, height: 2, background: B, margin: "32px auto 0", opacity: 0.5 }} />
        </Appear>
      </AbsoluteFill>
      <Bar rf={rf} dur={dur} />
    </AbsoluteFill>
  );
};

const LayoutQuestion: React.FC<LayoutProps> = ({ seg, rf, dur, fps, ts = 1, ss = 1, its = 1 }) => {
  const op  = fadeIn(rf, dur);
  const scB = spr(rf, fps, 0.7, 1, 0, 80, 20);
  const fs  = scaledAutoFs(seg.slide_title, 72, 40, ts);
  const items = seg.slide_items?.length > 0 ? seg.slide_items : [];
  return (
    <AbsoluteFill style={{ background: W }}>
      <GridBg />
      <div style={{ position: "absolute", top: "50%", left: "50%", transform: `translate(-40%,-50%) scale(${scB})`, fontFamily: F, fontSize: 560, fontWeight: 900, color: Y, opacity: 0.5, lineHeight: 1, userSelect: "none" }}>
        {seg.slide_icon || "？"}
      </div>
      <AbsoluteFill style={{ display: "flex", flexDirection: "column", justifyContent: "center", padding: "0 120px", opacity: op }}>
        <Appear rf={rf} fps={fps} delay={4} from="bottom">
          <div style={{ fontFamily: F, fontSize: fs, fontWeight: 900, color: B, lineHeight: 1.4, maxWidth: 1200 }}>
            {seg.slide_title}
          </div>
        </Appear>
        {seg.slide_sub && (
          <Appear rf={rf} fps={fps} delay={8} from="bottom">
            <div style={{ fontFamily: F, fontSize: Math.max(32, Math.round(fs * 0.5 * ss)), color: MG, marginTop: 20, maxWidth: 1200 }}>
              {seg.slide_sub}
            </div>
          </Appear>
        )}
        {items.length > 0 && (
          <Appear rf={rf} fps={fps} delay={12} from="left">
            <div style={{ display: "flex", gap: 24, marginTop: 40, flexWrap: "wrap" }}>
              {items.slice(0, 4).map((item, i) => (
                <div key={i} style={{ background: YL, borderRadius: 12, border: `2px solid ${Y}`, padding: "12px 24px" }}>
                  <span style={{ fontFamily: F, fontSize: Math.max(28, Math.round(fs * 0.4 * its)), fontWeight: 700, color: B }}>{item}</span>
                </div>
              ))}
            </div>
          </Appear>
        )}
      </AbsoluteFill>
      <Bar rf={rf} dur={dur} />
    </AbsoluteFill>
  );
};

const LayoutSection: React.FC<LayoutProps> = ({ seg, rf, dur, fps, ts = 1, ss = 1 }) => {
  const op  = fadeIn(rf, dur);
  const tx  = spr(rf, fps, -80);
  const fs  = scaledAutoFs(seg.slide_title, 72, 40, ts);
  return (
    <AbsoluteFill style={{ background: Y }}>
      <GridBg color="transparent" />
      <AbsoluteFill style={{ display: "flex", alignItems: "center", padding: "0 80px", gap: 60, opacity: op }}>
        <div style={{ transform: `translateX(${tx}px)` }}>
          {seg.slide_icon && <div style={{ fontSize: 80, textAlign: "center", marginBottom: -20 }}>{seg.slide_icon}</div>}
          <div style={{ fontFamily: F, fontSize: 300, fontWeight: 900, color: W, lineHeight: 1, WebkitTextStroke: `6px ${B}` }}>
            {seg.slide_number || "1"}
          </div>
        </div>
        <div style={{ flex: 1 }}>
          <Appear rf={rf} fps={fps} delay={6} from="right">
            <div style={{ background: W, borderRadius: 24, border: `3px solid ${B}`, padding: "48px 56px" }}>
              <div style={{ fontFamily: F, fontSize: fs, fontWeight: 900, color: B, lineHeight: 1.3 }}>{seg.slide_title}</div>
              {seg.slide_sub && <div style={{ fontFamily: F, fontSize: Math.round(38 * ss), color: MG, marginTop: 16 }}>{seg.slide_sub}</div>}
            </div>
          </Appear>
        </div>
      </AbsoluteFill>
      <Bar rf={rf} dur={dur} />
    </AbsoluteFill>
  );
};

const LayoutFeature: React.FC<LayoutProps> = ({ seg, rf, dur, fps, ts = 1, ss = 1, its = 1 }) => {
  const op = fadeIn(rf, dur);
  const sc = spr(rf, fps, 0.93, 1, 0, 160, 18);
  const fs = scaledAutoFs(seg.slide_title, 72, 40, ts);
  const subFs = Math.max(36, Math.round(fs * 0.5 * ss));
  const itemFs = Math.max(36, Math.round(fs * 0.5 * its));
  return (
    <AbsoluteFill style={{ background: W }}>
      <GridBg />
      <AbsoluteFill style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: "60px 100px", opacity: op }}>
        <div style={{ transform: `scale(${sc})`, width: "100%", maxWidth: 1300 }}>
          <YCard offset={10} radius={20}>
            {seg.slide_icon && <div style={{ fontSize: 60, marginBottom: 16 }}>{seg.slide_icon}</div>}
            <div style={{ fontFamily: F, fontSize: fs, fontWeight: 900, color: B, lineHeight: 1.3, marginBottom: 20 }}>
              {seg.slide_title}
            </div>
            {seg.slide_sub && (
              <div style={{ fontFamily: F, fontSize: subFs, color: B, lineHeight: 1.7 }}>{seg.slide_sub}</div>
            )}
            {seg.slide_items?.map((item, i) => (
              <div key={i} style={{ display: "flex", gap: 12, marginTop: 10 }}>
                <span style={{ color: Y, fontWeight: 900, fontSize: itemFs }}>•</span>
                <span style={{ fontFamily: F, fontSize: itemFs, color: B }}>{item}</span>
              </div>
            ))}
          </YCard>
        </div>
      </AbsoluteFill>
      <Bar rf={rf} dur={dur} />
    </AbsoluteFill>
  );
};

const LayoutSplit: React.FC<LayoutProps> = ({ seg, rf, dur, fps, ts = 1, ss = 1, its = 1 }) => {
  const op    = fadeIn(rf, dur);
  const items = seg.slide_items?.length >= 2 ? seg.slide_items : [seg.slide_title, seg.slide_sub || ""];
  const hFs   = scaledAutoFs(seg.slide_title, 52, 32, ts);
  const iFs   = scaledAutoFs(items[0] || "", 40, 28, its);
  return (
    <AbsoluteFill style={{ background: W }}>
      <GridBg />
      <AbsoluteFill style={{ display: "flex", flexDirection: "column", padding: "60px 80px", opacity: op }}>
        <Appear rf={rf} fps={fps} from="bottom">
          <div style={{ fontFamily: F, fontSize: hFs, fontWeight: 700, color: B, marginBottom: 40 }}>
            {seg.slide_icon && <span style={{ marginRight: 16 }}>{seg.slide_icon}</span>}
            {seg.slide_title}
          </div>
        </Appear>
        <div style={{ display: "flex", gap: 48, flex: 1 }}>
          {items.slice(0, 2).map((item, i) => (
            <Appear key={i} rf={rf} fps={fps} delay={i * 8} from="bottom">
              <div style={{ flex: 1 }}>
                <YCard minH={280} offset={8} radius={16}>
                  <div style={{ fontSize: 48, marginBottom: 12 }}>{seg.slide_icon || (i === 0 ? "📊" : "📄")}</div>
                  <div style={{ fontFamily: F, fontSize: iFs, fontWeight: 700, color: B, lineHeight: 1.6 }}>{item}</div>
                </YCard>
              </div>
            </Appear>
          ))}
        </div>
      </AbsoluteFill>
      <Bar rf={rf} dur={dur} />
    </AbsoluteFill>
  );
};

const LayoutFlow: React.FC<LayoutProps> = ({ seg, rf, dur, fps, ts = 1, ss = 1, its = 1 }) => {
  const op    = fadeIn(rf, dur);
  const items = seg.slide_items?.length >= 2 ? seg.slide_items : [seg.slide_title, "確認", "完了"];
  const hFs   = scaledAutoFs(seg.slide_title, 52, 32, ts);
  const iFs   = scaledAutoFs(items[0] || "", 38, 28, its);
  return (
    <AbsoluteFill style={{ background: W }}>
      <GridBg />
      <AbsoluteFill style={{ display: "flex", flexDirection: "column", padding: "50px 80px", opacity: op }}>
        <Appear rf={rf} fps={fps} from="bottom">
          <div style={{ fontFamily: F, fontSize: hFs, fontWeight: 700, color: B, marginBottom: 40 }}>
            {seg.slide_icon && <span style={{ marginRight: 16 }}>{seg.slide_icon}</span>}
            {seg.slide_title}
          </div>
        </Appear>
        <div style={{ flex: 1, display: "flex", alignItems: "center", background: YL, borderRadius: 20, padding: "40px 60px", gap: 20 }}>
          {items.slice(0, 3).map((item, i) => (
            <React.Fragment key={i}>
              <Appear rf={rf} fps={fps} delay={i * 8} from="scale">
                <div style={{ background: W, borderRadius: 16, border: `2px solid ${B}`, padding: "28px 32px", flex: 1, minHeight: 160 }}>
                  <div style={{ fontFamily: F, fontSize: iFs, fontWeight: 700, color: B, lineHeight: 1.5 }}>{item}</div>
                </div>
              </Appear>
              {i < Math.min(items.length, 3) - 1 && (
                <div style={{ color: Y, fontSize: 48, fontWeight: 900, opacity: interpolate(rf, [i * 8 + 14, i * 8 + 22], [0, 1], cl) }}>→</div>
              )}
            </React.Fragment>
          ))}
        </div>
      </AbsoluteFill>
      <Bar rf={rf} dur={dur} />
    </AbsoluteFill>
  );
};

const LayoutTimeline: React.FC<LayoutProps> = ({ seg, rf, dur, fps, ts = 1, its = 1 }) => {
  const op    = fadeIn(rf, dur);
  const items = seg.slide_items?.length >= 2 ? seg.slide_items : ["2019年: 開始", "2025年: 移行", "2027年: 完全適用"];
  const lw    = interpolate(rf, [8, 35], [0, 1760], cl);
  const hFs   = scaledAutoFs(seg.slide_title, 52, 32, ts);
  const labelFs = Math.round(38 * its);
  const descFs  = Math.round(30 * its);
  return (
    <AbsoluteFill style={{ background: W }}>
      <GridBg />
      <AbsoluteFill style={{ display: "flex", flexDirection: "column", padding: "60px 80px", opacity: op }}>
        <Appear rf={rf} fps={fps} from="bottom">
          <div style={{ fontFamily: F, fontSize: hFs, fontWeight: 700, color: B, marginBottom: 80 }}>
            {seg.slide_icon && <span style={{ marginRight: 16 }}>{seg.slide_icon}</span>}
            {seg.slide_title}
          </div>
        </Appear>
        <div style={{ position: "relative", height: 200 }}>
          <div style={{ position: "absolute", top: 32, left: 0, width: lw, height: 3, background: Y, borderRadius: 2 }} />
          <div style={{ position: "absolute", top: 19, left: lw - 2, color: Y, fontSize: 30, opacity: interpolate(rf, [30, 38], [0, 1], cl) }}>▶</div>
          {items.slice(0, 4).map((item, i) => {
            const parts = item.split(/[:：]/);
            const label = parts[0]?.trim() || "";
            const desc  = parts[1]?.trim() || "";
            const xPct  = 10 + i * (80 / Math.max(items.length - 1, 1));
            const dotOp = interpolate(rf, [i * 6 + 8, i * 6 + 16], [0, 1], cl);
            return (
              <React.Fragment key={i}>
                <div style={{ position: "absolute", top: 22, left: `${xPct}%`, transform: "translateX(-50%)", width: 20, height: 20, borderRadius: "50%", background: Y, border: `3px solid ${B}`, opacity: dotOp }} />
                <Appear rf={rf} fps={fps} delay={i * 6 + 8} from="bottom">
                  <div style={{ position: "absolute", top: 58, left: `${xPct}%`, transform: "translateX(-50%)", textAlign: "center", width: 280 }}>
                    <div style={{ fontFamily: F, fontSize: labelFs, fontWeight: 900, color: B, border: `2px solid ${B}`, borderRadius: 8, padding: "6px 16px", display: "inline-block", background: W }}>{label}</div>
                    <div style={{ fontFamily: F, fontSize: descFs, color: MG, marginTop: 8 }}>{desc}</div>
                  </div>
                </Appear>
              </React.Fragment>
            );
          })}
        </div>
      </AbsoluteFill>
      <Bar rf={rf} dur={dur} />
    </AbsoluteFill>
  );
};

const LayoutBullets: React.FC<LayoutProps> = ({ seg, rf, dur, fps, ts = 1, ss = 1, its = 1 }) => {
  const op    = fadeIn(rf, dur);
  const items = seg.slide_items?.length > 0 ? seg.slide_items : [seg.slide_sub || ""].filter(Boolean);
  const hFs   = scaledAutoFs(seg.slide_title, 60, 40, ts);
  const iFs   = scaledAutoFs(items[0] || "", 40, 28, its);
  return (
    <AbsoluteFill style={{ background: W }}>
      <GridBg />
      <AbsoluteFill style={{ display: "flex", flexDirection: "column", padding: "80px 100px", opacity: op }}>
        <Appear rf={rf} fps={fps} from="bottom">
          <div style={{ fontFamily: F, fontSize: hFs, fontWeight: 900, color: B, marginBottom: 52, lineHeight: 1.3 }}>
            {seg.slide_icon && <span style={{ marginRight: 16 }}>{seg.slide_icon}</span>}
            {seg.slide_title}
          </div>
        </Appear>
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {items.slice(0, 4).map((item, i) => (
            <Appear key={i} rf={rf} fps={fps} delay={i * 8 + 6} from="left">
              <div style={{ display: "flex", alignItems: "flex-start", gap: 20 }}>
                <div style={{ width: 12, height: 12, borderRadius: "50%", background: B, marginTop: 14, flexShrink: 0 }} />
                <div style={{ fontFamily: F, fontSize: iFs, color: B, lineHeight: 1.7 }}>{item}</div>
              </div>
            </Appear>
          ))}
        </div>
      </AbsoluteFill>
      <Bar rf={rf} dur={dur} />
    </AbsoluteFill>
  );
};

const LayoutCards: React.FC<LayoutProps> = ({ seg, rf, dur, fps, ts = 1, ss = 1, its = 1 }) => {
  const op    = fadeIn(rf, dur);
  const items = seg.slide_items?.length >= 3 ? seg.slide_items : [];
  const hFs   = scaledAutoFs(seg.slide_title, 52, 32, ts);
  const CARD_COLORS = ["#DBEAFE", "#FEF3C0", "#D1FAE5", "#FDE2E2", "#E8DAEF", "#FCE4EC"];
  const CARD_BORDERS = ["#3B82F6", "#F59E0B", "#10B981", "#EF4444", "#8B5CF6", "#EC4899"];
  return (
    <AbsoluteFill style={{ background: W }}>
      <GridBg />
      <AbsoluteFill style={{ display: "flex", flexDirection: "column", padding: "60px 80px", opacity: op }}>
        <Appear rf={rf} fps={fps} from="bottom">
          <div style={{ fontFamily: F, fontSize: hFs, fontWeight: 900, color: B, marginBottom: 40, lineHeight: 1.3 }}>
            {seg.slide_icon && <span style={{ marginRight: 16 }}>{seg.slide_icon}</span>}
            {seg.slide_title}
          </div>
        </Appear>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 28, flex: 1, alignContent: "flex-start" }}>
          {items.slice(0, 6).map((item, i) => {
            // 旧データ互換: 先頭の "01: " のような数字+コロンを除去してから分割
            const cleaned = item.replace(/^\d+[:：]\s*/, "");
            const sepIdx = cleaned.search(/[:：]/);
            const titlePart = (sepIdx >= 0 ? cleaned.slice(0, sepIdx) : cleaned).trim();
            const descPart  = sepIdx >= 0 ? cleaned.slice(sepIdx + 1).trim() : "";
            const iFs = scaledAutoFs(titlePart || cleaned, 36, 28, its);
            const bg = CARD_COLORS[i % CARD_COLORS.length];
            const border = CARD_BORDERS[i % CARD_BORDERS.length];
            return (
              <Appear key={i} rf={rf} fps={fps} delay={i * 5} from="scale">
                <div style={{ width: items.length <= 3 ? "calc(33.3% - 20px)" : "calc(33.3% - 20px)", minHeight: 180 }}>
                  <div style={{ background: bg, borderRadius: 20, border: `3px solid ${border}`, padding: "28px 24px", height: "100%" }}>
                    <div style={{ fontFamily: F, fontSize: 52, fontWeight: 900, color: border, marginBottom: 12, opacity: 0.7 }}>
                      {String(i + 1).padStart(2, "0")}
                    </div>
                    <div style={{ fontFamily: F, fontSize: iFs, fontWeight: 700, color: B, lineHeight: 1.5 }}>{titlePart}</div>
                    {descPart && <div style={{ fontFamily: F, fontSize: Math.max(28, Math.round(iFs * 0.8)), color: "#555", marginTop: 8 }}>{descPart}</div>}
                  </div>
                </div>
              </Appear>
            );
          })}
        </div>
      </AbsoluteFill>
      <Bar rf={rf} dur={dur} />
    </AbsoluteFill>
  );
};

// ─── ルーター ──────────────────────────────────────────
const SlideRouter: React.FC<LayoutProps> = (p) => {
  switch (p.seg.slide_layout) {
    case "title":    return <LayoutTitle    {...p} />;
    case "question": return <LayoutQuestion {...p} />;
    case "section":  return <LayoutSection  {...p} />;
    case "split":    return <LayoutSplit    {...p} />;
    case "flow":     return <LayoutFlow     {...p} />;
    case "timeline": return <LayoutTimeline {...p} />;
    case "bullets":  return <LayoutBullets  {...p} />;
    case "cards":    return <LayoutCards    {...p} />;
    default:         return <LayoutFeature  {...p} />;
  }
};

// ─── 複数音声ファイルを連続再生 ────────────────────────
const MultiAudio: React.FC<{ seg: Seg; fps: number; segStart: number }> = ({ seg, fps, segStart }) => {
  const files = seg.audio_files ?? [seg.audio_file];
  const durations = seg.run_durations_ms;
  let cumulativeMs = 0;
  return (
    <>
      {files.map((f, i) => {
        const from = Math.round(segStart + offset * fps / 1000);
        const wrapped = (
          <Sequence key={i} from={from} layout="none">
            <Audio src={f} />
          </Sequence>
        );
        // 次のオフセット（duration はわからないので均等割り）
        cumulativeMs += durations[i] ?? 0;
        return wrapped;
      })}
    </>
  );
};

// ─── イントロ ──────────────────────────────────────────
const Intro: React.FC<{ frame: number; fps: number }> = ({ frame, fps }) => {
  const op = interpolate(frame, [0, 15, 55, 70], [0, 1, 1, 0], cl);
  const sc = spring({ frame, fps, config: { stiffness: 180, damping: 20 }, from: 1.04, to: 1 });
  const lw = interpolate(frame, [8, 28], [0, 200], cl);
  return (
    <div style={{ position: "absolute", inset: 0, background: W, opacity: op, transform: `scale(${sc})` }}>
      <GridBg />
      <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
        <div style={{ width: lw, height: 1.5, background: B, marginBottom: 48 }} />
        <div style={{ fontFamily: F, fontSize: 28, fontWeight: 300, color: MG, letterSpacing: "0.5em" }}>音　声　記　録</div>
        <div style={{ width: lw, height: 1.5, background: B, marginTop: 48 }} />
      </div>
    </div>
  );
};

const Trans: React.FC<{ rf: number }> = ({ rf }) => {
  if (rf <= 0) return null;
  const h = interpolate(rf, [0, 6, 12, 20], [0, 1080, 1080, 0], cl);
  return <div style={{ position: "absolute", top: 0, left: "50%", transform: "translateX(-50%)", width: 2, height: h, background: Y }} />;
};

// ─── メイン ────────────────────────────────────────────
export const SubtitleVideo: React.FC<Props> = ({ fps, originalAudio, segments, layoutTitleScales, layoutSubScales, layoutItemScales }) => {
  const frame = useCurrentFrame();
  const ms2f  = (ms: number) => Math.round(ms / 1000 * fps);

  return (
    <AbsoluteFill style={{ background: W }}>
      {segments.map((seg, idx) => {
        const sf  = ms2f(seg.start_ms);
        const dur = ms2f(seg.duration_ms) + ms2f(200);
        const rf  = frame - sf;
        const layout = seg.slide_layout || "feature";
        const ts  = layoutTitleScales?.[layout] ?? 1;
        const ss  = layoutSubScales?.[layout] ?? 1;
        const its = layoutItemScales?.[layout] ?? 1;

        return (
          <Sequence key={seg.index} from={sf} durationInFrames={dur + 20} layout="none">
            <AbsoluteFill style={{ background: W }}>
              {rf >= 0 && (
                <>
                  <SlideRouter seg={seg} rf={rf} dur={dur} fps={fps} ts={ts} ss={ss} its={its} />
                  <Trans rf={rf - dur + 4} />
                </>
              )}
              {/* 複数音声ファイルを順番に再生 */}
              <MultiAudio seg={seg} fps={fps} segStart={sf} />
            </AbsoluteFill>
          </Sequence>
        );
      })}

      {segments.length > 0 && ms2f(segments[0].start_ms) > fps && (
        <Sequence from={0} durationInFrames={ms2f(segments[0].start_ms)} layout="none">
          <Intro frame={frame} fps={fps} />
        </Sequence>
      )}
      {originalAudio && <Audio src={originalAudio} volume={0} />}
    </AbsoluteFill>
  );
};
