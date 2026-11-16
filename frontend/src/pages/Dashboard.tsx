import { RATING_COLOR, Spark } from "../charts";
import { Bar, Err, Loading, Pill, Stat, go, money, pct, useApi } from "../ui";

export default function Dashboard() {
  const pf = useApi("/v1/metrics/portfolio");
  const rd = useApi<any[]>("/v1/metrics/readiness");
  const q = useApi<any[]>("/v1/review-queue");
  const snaps = useApi<any[]>("/v1/snapshots");
  const p = pf.data;
  if (pf.error) return <Err e={pf.error} />;
  if (!p) return <Loading />;
  const t = p.targets || { hours_year1: 100, hours_year2: 65 };
  const overdueSnaps = (snaps.data || []).filter((s) => ["drafted", "intake"].includes(s.status) && s.due_at && new Date(s.due_at) < new Date());
  const ready = rd.data || [];
  const avgScore = ready.length ? Math.round(ready.reduce((a, r) => a + r.score, 0) / ready.length) : null;
  return (
    <>
      <h1>Delivery dashboard</h1>
      <p className="sub">Year 1: map every hour. Year 2: {t.hours_year1} → {t.hours_year2} hours per client, with experts signing every output.</p>
      <div className="grid g4">
        <Stat k="Average readiness score" v={avgScore ?? "—"} n={`${ready.filter((r) => r.rating === "Critical").length} engagements critical`} />
        <Stat k="Awaiting expert review" v={q.data?.length ?? "…"} n={<a href="#/queue">open queue →</a>} />
        <Stat k="Contract value" v={money(p.total_contract_value_usd)} n={`contribution ${money(p.total_contribution_usd)}`} />
        <Stat k="Snapshots" v={(snaps.data || []).length} n={overdueSnaps.length ? <span style={{ color: "var(--accent)" }}>{overdueSnaps.length} past 3-day SLA</span> : "all within SLA"} />
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="spread"><h2>Audit readiness by engagement</h2><span className="small muted">lowest first · trend = % of controls implemented, last 12 months</span></div>
        {!rd.data ? <Loading /> : <table>
          <thead><tr><th>Engagement</th><th>Frameworks</th><th>Score</th><th style={{ width: 170 }}>Implemented</th><th>Trend</th><th>Open exceptions</th><th>Model</th></tr></thead>
          <tbody>{ready.map((r) => (
            <tr key={r.engagement_id} className="click" onClick={() => go(`/engagements/${r.engagement_id}`)}>
              <td><b>{r.name}</b></td><td className="small">{r.frameworks.join(", ")}</td>
              <td><span className="pill" style={{ color: "var(--ink)" }}><i style={{ width: 8, height: 8, borderRadius: 4, background: RATING_COLOR[r.rating] }} />{Math.round(r.score)} · {r.rating}</span></td>
              <td><Bar v={r.implementation_pct || 0} /><span className="small faint">{r.implementation_pct ?? 0}%</span></td>
              <td><Spark values={r.trend} /></td><td>{r.open_exceptions}</td><td className="small faint">{r.mode === "type2" ? "Type II" : "Readiness"}</td>
            </tr>))}</tbody></table>}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="spread"><h2>Delivery economics</h2><span className="muted small">AI cost to date {money(p.total_ai_cost_usd)}</span></div>
        <table>
          <thead><tr><th>Engagement</th><th>Status</th><th>Hours (eng / expert)</th><th style={{ width: 180 }}>vs year-1 target</th><th>AI accepted</th><th>Contribution</th></tr></thead>
          <tbody>{p.engagements.map((e: any) => (
            <tr key={e.engagement_id} className="click" onClick={() => go(`/engagements/${e.engagement_id}`)}>
              <td>{e.name}</td><td><Pill s={e.status} /></td>
              <td>{e.hours_total} <span className="muted small">({e.hours_engineer} / {e.hours_expert})</span></td>
              <td><Bar v={(e.hours_total / t.hours_year1) * 100} /></td>
              <td>{pct(e.ai_acceptance_rate)}</td>
              <td>{money(e.contribution_usd)} <span className="muted small">{e.contribution_margin != null ? pct(e.contribution_margin) : ""}</span></td>
            </tr>))}</tbody>
        </table>
      </div>
    </>
  );
}
