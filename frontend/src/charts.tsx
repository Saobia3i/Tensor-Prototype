// Dependency-free SVG charts. Every plotted chart has a hover layer; status colors always ship with labels.
import { useRef, useState, type ReactNode } from "react";

export const STATUS_META: Record<string, { label: string; color: string }> = {
  effective: { label: "Effective", color: "var(--st-good)" },
  needs_attention: { label: "Needs attention", color: "var(--st-warning)" },
  ineffective: { label: "Ineffective", color: "var(--st-critical)" },
  not_tested: { label: "Not tested", color: "var(--st-serious)" },
  not_implemented: { label: "Not implemented", color: "var(--st-neutral)" },
  not_applicable: { label: "Not applicable", color: "var(--st-na)" },
};
export const RATING_COLOR: Record<string, string> = { Ready: "var(--st-good)", "At risk": "var(--st-warning)", Critical: "var(--st-critical)" };

type Tip = { x: number; y: number; body: ReactNode } | null;
function useTip() {
  const ref = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<Tip>(null);
  const show = (e: React.MouseEvent, body: ReactNode) => {
    const r = ref.current?.getBoundingClientRect();
    if (r) setTip({ x: e.clientX - r.left, y: e.clientY - r.top, body });
  };
  const el = tip ? <div className="tip" style={{ left: tip.x, top: tip.y }}>{tip.body}</div> : null;
  return { ref, show, hide: () => setTip(null), el };
}

/** Readiness gauge: hero number on a 240° arc. */
export function Gauge({ score, rating, size = 190 }: { score: number; rating: string; size?: number }) {
  const r = size / 2 - 14, cx = size / 2, cy = size / 2 + 6;
  const start = 150, sweep = 240;
  const pt = (deg: number) => [cx + r * Math.cos((deg * Math.PI) / 180), cy + r * Math.sin((deg * Math.PI) / 180)];
  const arc = (from: number, to: number) => {
    const [x1, y1] = pt(from), [x2, y2] = pt(to);
    return `M ${x1} ${y1} A ${r} ${r} 0 ${to - from > 180 ? 1 : 0} 1 ${x2} ${y2}`;
  };
  const end = start + (sweep * Math.max(0, Math.min(100, score))) / 100;
  return (
    <div className="chart" style={{ width: size, margin: "0 auto" }} role="img" aria-label={`Readiness score ${score} of 100, ${rating}`}>
      <svg width={size} height={size * 0.82} viewBox={`0 0 ${size} ${size * 0.82}`}>
        <path d={arc(start, start + sweep)} stroke="var(--soft)" strokeWidth={12} fill="none" strokeLinecap="round" />
        {score > 0 && <path d={arc(start, end)} stroke={RATING_COLOR[rating] || "var(--series-1)"} strokeWidth={12} fill="none" strokeLinecap="round" />}
      </svg>
      <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", paddingTop: 8 }}>
        <div className="hero">{Math.round(score)}</div>
        <div className="small muted">of 100</div>
        <span className="pill" style={{ marginTop: 6, color: "var(--ink)" }}><i style={{ width: 8, height: 8, borderRadius: 4, background: RATING_COLOR[rating] }} />{rating}</span>
      </div>
    </div>
  );
}

/** Donut of control statuses with 2px surface gaps and a full legend. */
export function Donut({ data, size = 170, center }: { data: { status: string; count: number }[]; size?: number; center?: ReactNode }) {
  const t = useTip();
  const total = data.reduce((a, d) => a + d.count, 0) || 1;
  const r = size / 2 - 8, inner = r - 22, cx = size / 2, cy = size / 2;
  let acc = 0;
  const segs = data.filter((d) => d.count > 0).map((d) => {
    const a0 = (acc / total) * Math.PI * 2 - Math.PI / 2;
    acc += d.count;
    const a1 = (acc / total) * Math.PI * 2 - Math.PI / 2;
    const big = a1 - a0 > Math.PI ? 1 : 0;
    const p = (rad: number, rr: number) => `${cx + rr * Math.cos(rad)} ${cy + rr * Math.sin(rad)}`;
    const full = d.count === total;
    const path = full ? `M ${cx - r} ${cy} a ${r} ${r} 0 1 0 ${2 * r} 0 a ${r} ${r} 0 1 0 ${-2 * r} 0 M ${cx - inner} ${cy} a ${inner} ${inner} 0 1 1 ${2 * inner} 0 a ${inner} ${inner} 0 1 1 ${-2 * inner} 0`
      : `M ${p(a0, r)} A ${r} ${r} 0 ${big} 1 ${p(a1, r)} L ${p(a1, inner)} A ${inner} ${inner} 0 ${big} 0 ${p(a0, inner)} Z`;
    return { ...d, path };
  });
  return (
    <div>
      <div className="chart" ref={t.ref} style={{ width: size, margin: "0 auto" }}>
        <svg width={size} height={size} role="img" aria-label="Control status distribution">
          {segs.map((s) => <path key={s.status} d={s.path} fill={STATUS_META[s.status]?.color} stroke="var(--surface-solid)" strokeWidth={2} fillRule="evenodd"
            onMouseMove={(e) => t.show(e, <><b>{STATUS_META[s.status]?.label}</b> · {s.count} ({Math.round((100 * s.count) / total)}%)</>)} onMouseLeave={t.hide} />)}
        </svg>
        {center && <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", pointerEvents: "none" }}>{center}</div>}
        {t.el}
      </div>
      <div className="legend" style={{ justifyContent: "center" }}>{data.map((d) =>
        <span key={d.status}><i style={{ background: STATUS_META[d.status]?.color }} />{STATUS_META[d.status]?.label} <b style={{ color: "var(--ink)" }}>{d.count}</b></span>)}</div>
    </div>
  );
}

/** Radar with up to 2 series (0–100 scale). */
export function Radar({ axes, series, size = 300 }: { axes: string[]; series: { name: string; values: (number | null)[]; color: string }[]; size?: number }) {
  const t = useTip();
  const n = axes.length;
  if (n < 5) return <GroupBars axes={axes} series={series} />;  // a radar with < 5 spokes reads as a sliver
  const cx = size / 2, cy = size / 2, R = size / 2 - 58;
  const pt = (i: number, v: number) => {
    const a = (Math.PI * 2 * i) / n - Math.PI / 2;
    return [cx + (R * v) / 100 * Math.cos(a), cy + (R * v) / 100 * Math.sin(a)];
  };
  return (
    <div>
      <div className="chart" ref={t.ref} style={{ width: size, margin: "0 auto" }}>
        <svg width={size} height={size} role="img" aria-label="Coverage radar by criterion">
          {[25, 50, 75, 100].map((g) => <polygon key={g} points={axes.map((_, i) => pt(i, g).join(",")).join(" ")} fill="none" className="gridline" />)}
          {axes.map((a, i) => {
            const [x, y] = pt(i, 100), [lx, ly] = pt(i, 118);
            return <g key={a}><line x1={cx} y1={cy} x2={x} y2={y} className="gridline" />
              <text x={lx} y={ly} textAnchor={Math.abs(lx - cx) < 8 ? "middle" : lx > cx ? "start" : "end"} dominantBaseline="middle">{(() => { const s = a.length > 18 ? a.replace(/\s*\(.*\)$/, "") : a; return s.length > 18 ? s.slice(0, 17) + "…" : s; })()}</text></g>;
          })}
          {series.map((s) => <polygon key={s.name} points={s.values.map((v, i) => pt(i, v ?? 0).join(",")).join(" ")} fill={s.color} fillOpacity={0.14} stroke={s.color} strokeWidth={2} />)}
          {series.map((s) => s.values.map((v, i) => {
            const [x, y] = pt(i, v ?? 0);
            return <circle key={s.name + i} cx={x} cy={y} r={4} fill={s.color} stroke="var(--surface-solid)" strokeWidth={2}
              onMouseMove={(e) => t.show(e, <><b>{axes[i]}</b><br />{series.map((ss) => <div key={ss.name}>{ss.name}: {ss.values[i] ?? "—"}%</div>)}</>)} onMouseLeave={t.hide} />;
          }))}
        </svg>
        {t.el}
      </div>
      {series.length > 1 && <div className="legend" style={{ justifyContent: "center" }}>{series.map((s) => <span key={s.name}><i style={{ background: s.color }} />{s.name}</span>)}</div>}
    </div>
  );
}

/** Grouped horizontal bars: the honest fallback when there are too few groups for a radar. */
export function GroupBars({ axes, series }: { axes: string[]; series: { name: string; values: (number | null)[]; color: string }[] }) {
  const t = useTip();
  return (
    <div className="chart" ref={t.ref}>
      {axes.map((a, i) => <div key={a} style={{ marginBottom: 12 }}>
        <div className="small" style={{ marginBottom: 4 }}>{a}</div>
        {series.map((s) => <div key={s.name} style={{ display: "grid", gridTemplateColumns: "1fr 44px", gap: 8, alignItems: "center", marginBottom: 3 }}
          onMouseMove={(e) => t.show(e, <><b>{a}</b> · {s.name}: {s.values[i] ?? "—"}%</>)} onMouseLeave={t.hide}>
          <div style={{ height: 9, borderRadius: 99, background: "var(--soft)" }}><div style={{ width: `${s.values[i] ?? 0}%`, height: "100%", borderRadius: 99, background: s.color }} /></div>
          <span className="small" style={{ textAlign: "right" }}>{s.values[i] == null ? "—" : `${Math.round(s.values[i]!)}%`}</span>
        </div>)}
      </div>)}
      {series.length > 1 && <div className="legend">{series.map((s) => <span key={s.name}><i style={{ background: s.color }} />{s.name}</span>)}</div>}
      {t.el}
    </div>
  );
}

/** Horizontal bars, 0–100%. */
export function BarList({ rows, color = "var(--series-1)" }: { rows: { label: string; value: number | null; note?: string }[]; color?: string }) {
  const t = useTip();
  return (
    <div className="chart" ref={t.ref}>
      {rows.map((r) => (
        <div key={r.label} style={{ display: "grid", gridTemplateColumns: "150px 1fr 46px", gap: 10, alignItems: "center", marginBottom: 8 }}
          onMouseMove={(e) => t.show(e, <><b>{r.label}</b> · {r.value ?? "—"}%{r.note ? <><br /><span className="muted">{r.note}</span></> : null}</>)} onMouseLeave={t.hide}>
          <span className="small" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.label}</span>
          <div style={{ height: 10, borderRadius: 99, background: "var(--soft)" }}><div style={{ width: `${r.value ?? 0}%`, height: "100%", borderRadius: 99, background: color }} /></div>
          <span className="small" style={{ textAlign: "right" }}>{r.value == null ? "—" : `${Math.round(r.value)}%`}</span>
        </div>))}
      {t.el}
    </div>
  );
}

/** Area/line over time with crosshair tooltip. Single y-axis. */
export function TrendLine({ points, height = 170, unit = "%", max }: { points: { x: string; y: number; extra?: ReactNode }[]; height?: number; unit?: string; max?: number }) {
  const t = useTip();
  const [hi, setHi] = useState<number | null>(null);
  const W = 560, H = height, pl = 34, pr = 10, pt = 10, pb = 22;
  const ymax = max ?? Math.max(1, ...points.map((p) => p.y)) * 1.1;
  const X = (i: number) => pl + (i * (W - pl - pr)) / Math.max(1, points.length - 1);
  const Y = (v: number) => pt + (H - pt - pb) * (1 - v / ymax);
  const line = points.map((p, i) => `${i ? "L" : "M"} ${X(i)} ${Y(p.y)}`).join(" ");
  const area = `${line} L ${X(points.length - 1)} ${Y(0)} L ${X(0)} ${Y(0)} Z`;
  const ticks = [0, ymax / 2, ymax].map((v) => Math.round(v));
  return (
    <div className="chart" ref={t.ref}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" role="img" aria-label="Trend over time"
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const i = Math.round(((e.clientX - r.left) / r.width * W - pl) / ((W - pl - pr) / Math.max(1, points.length - 1)));
          const k = Math.max(0, Math.min(points.length - 1, i));
          setHi(k);
          t.show(e, <><b>{points[k].x}</b> · {points[k].y}{unit}{points[k].extra ? <><br />{points[k].extra}</> : null}</>);
        }} onMouseLeave={() => { setHi(null); t.hide(); }}>
        <defs><linearGradient id="trendfill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="var(--series-1)" stopOpacity={0.35} /><stop offset="1" stopColor="var(--series-1)" stopOpacity={0} /></linearGradient></defs>
        {ticks.map((v) => <g key={v}><line x1={pl} x2={W - pr} y1={Y(v)} y2={Y(v)} className="gridline" /><text x={pl - 6} y={Y(v)} textAnchor="end" dominantBaseline="middle">{v}</text></g>)}
        <path d={area} fill="url(#trendfill)" />
        <path d={line} fill="none" stroke="var(--series-1)" strokeWidth={2} vectorEffect="non-scaling-stroke" />
        {points.map((p, i) => (points.length - 1 - i) % 2 === 0 &&
          <text key={p.x} x={X(i)} y={H - 6} textAnchor={i === points.length - 1 ? "end" : "middle"}>{p.x.slice(2)}</text>)}
        {hi != null && <><line x1={X(hi)} x2={X(hi)} y1={pt} y2={Y(0)} stroke="var(--muted)" strokeDasharray="3 3" />
          <circle cx={X(hi)} cy={Y(points[hi].y)} r={4.5} fill="var(--series-1)" stroke="var(--surface-solid)" strokeWidth={2} /></>}
      </svg>
      {t.el}
    </div>
  );
}

/** Vertical columns (counts per period). */
export function Columns({ points, height = 120, color = "var(--series-2)", label }: { points: { x: string; y: number }[]; height?: number; color?: string; label: string }) {
  const t = useTip();
  const max = Math.max(1, ...points.map((p) => p.y));
  return (
    <div className="chart" ref={t.ref} role="img" aria-label={label}>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height }}>
        {points.map((p) => <div key={p.x} style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "flex-end", height: "100%" }}
          onMouseMove={(e) => t.show(e, <><b>{p.x}</b> · {p.y} {label}</>)} onMouseLeave={t.hide}>
          <div style={{ height: `${(100 * p.y) / max}%`, minHeight: p.y ? 3 : 0, background: color, borderRadius: "4px 4px 0 0" }} />
        </div>)}
      </div>
      <div style={{ display: "flex", gap: 4 }}>{points.map((p, i) => <span key={p.x} className="faint" style={{ flex: 1, fontSize: 10, textAlign: "center" }}>{i % 2 === points.length % 2 || i === points.length - 1 ? p.x.slice(5) : ""}</span>)}</div>
      {t.el}
    </div>
  );
}

/** Stacked horizontal bars by status (e.g. risk exposure). 2px gaps between segments. */
export function StackedBars({ rows }: { rows: { label: string; parts: Record<string, number> }[] }) {
  const t = useTip();
  const order = Object.keys(STATUS_META);
  const max = Math.max(1, ...rows.map((r) => Object.values(r.parts).reduce((a, b) => a + b, 0)));
  return (
    <div className="chart" ref={t.ref}>
      {rows.map((r) => {
        const total = Object.values(r.parts).reduce((a, b) => a + b, 0);
        return <div key={r.label} style={{ display: "grid", gridTemplateColumns: "70px 1fr 34px", gap: 10, alignItems: "center", marginBottom: 9 }}>
          <span className="small" style={{ textTransform: "capitalize" }}>{r.label}</span>
          <div style={{ display: "flex", gap: 2, height: 14, width: `${(100 * total) / max}%` }}>
            {order.filter((k) => r.parts[k]).map((k) => <div key={k} style={{ flex: r.parts[k], background: STATUS_META[k].color, borderRadius: 4 }}
              onMouseMove={(e) => t.show(e, <><b>{r.label} risk</b> · {STATUS_META[k].label}: {r.parts[k]}</>)} onMouseLeave={t.hide} />)}
          </div>
          <span className="small muted" style={{ textAlign: "right" }}>{total}</span>
        </div>;
      })}
      <div className="legend">{order.map((k) => <span key={k}><i style={{ background: STATUS_META[k].color }} />{STATUS_META[k].label}</span>)}</div>
      {t.el}
    </div>
  );
}

/** Compliance matrix: one cell per control, grouped. */
export function Matrix({ groups }: { groups: { name: string; cells: { code: string; title: string; status: string; owner?: string }[] }[] }) {
  const t = useTip();
  return (
    <div className="chart" ref={t.ref}>
      {groups.map((g) => <div key={g.name} style={{ marginBottom: 10 }}>
        <div className="small muted" style={{ marginBottom: 4 }}>{g.name} <span className="faint">({g.cells.length})</span></div>
        <div className="matrix">{g.cells.map((c) => <div key={c.code} className="cell" style={{ background: STATUS_META[c.status]?.color }}
          onMouseMove={(e) => t.show(e, <><b>{c.code}</b> · {STATUS_META[c.status]?.label}<br /><span className="muted">{c.title}</span>{c.owner ? <><br />Owner: {c.owner}</> : null}</>)} onMouseLeave={t.hide} />)}</div>
      </div>)}
      <div className="legend">{Object.entries(STATUS_META).map(([k, v]) => <span key={k}><i style={{ background: v.color }} />{v.label}</span>)}</div>
      {t.el}
    </div>
  );
}

/** Tiny sparkline (single series, no axes) for tables. */
export function Spark({ values, width = 110, height = 28 }: { values: number[]; width?: number; height?: number }) {
  if (!values.length) return null;
  const max = Math.max(1, ...values);
  const pts = values.map((v, i) => `${(i * width) / Math.max(1, values.length - 1)},${height - 2 - ((height - 4) * v) / max}`).join(" ");
  const last = values[values.length - 1];
  return <svg width={width} height={height} role="img" aria-label={`Trend, latest ${last}%`}>
    <polyline points={pts} fill="none" stroke="var(--series-1)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
    <circle cx={width} cy={height - 2 - ((height - 4) * last) / max} r={3} fill="var(--series-1)" />
  </svg>;
}

/** Weighted component bars for the readiness score. */
export function Components({ items }: { items: { label: string; weight: number; score: number }[] }) {
  return <div>{items.map((c) => <div key={c.label} style={{ marginBottom: 9 }}>
    <div className="small" style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 8 }}><span>{c.label} <span className="faint">· {Math.round(c.weight * 100)}%</span></span><b>{Math.round(c.score)}%</b></div>
    <div className="bar" style={{ marginTop: 3 }}><div style={{ width: `${c.score}%`, background: c.score >= 90 ? "var(--st-good)" : c.score >= 70 ? "var(--st-warning)" : "var(--st-critical)" }} /></div>
  </div>)}</div>;
}
