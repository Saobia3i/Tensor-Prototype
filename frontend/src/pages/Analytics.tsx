import { BarList, Columns, Components, Donut, Gauge, Matrix, Radar, StackedBars, TrendLine } from "../charts";
import { Err, Loading, Pill, Stat, d, dt, useApi } from "../ui";

export function useAnalytics(engId: string) {
  return useApi(`/v1/engagements/${engId}/analytics`);
}

export default function AnalyticsOverview({ eng }: { eng: any }) {
  const a = useAnalytics(eng.id);
  const ledger = useApi(`/v1/engagements/${eng.id}/trust-ledger`);
  if (a.error) return <Err e={a.error} />;
  if (!a.data) return <Loading what="Computing readiness" />;
  const x = a.data, k = x.kpis;
  const groups: Record<string, any[]> = {};
  x.controls.forEach((c: any) => (c.groups.length ? c.groups : ["Other"]).forEach((g: string) => (groups[g] ||= []).push(c)));
  const type2 = x.mode === "type2";
  return (
    <>
      <div className="grid" style={{ gridTemplateColumns: "minmax(0,300px) minmax(0,1fr)" }}>
        <div className="card">
          <div className="spread"><h2>Audit readiness</h2><span className="pill">{type2 ? "Type II model" : "Readiness model"}</span></div>
          <Gauge score={x.readiness.score} rating={x.readiness.rating} />
          <Components items={x.readiness.components} />
          <p className="faint small" style={{ marginBottom: 0 }}>Weighted from records as of {x.as_of}. Ready ≥ 90 · At risk 70–89 · Critical &lt; 70.</p>
        </div>
        <div className="grid" style={{ alignContent: "start" }}>
          <div className="grid g4">
            <Stat k="Controls implemented" v={`${k.controls_implemented}/${k.controls_in_scope}`} n={`${k.implementation_pct ?? 0}% of scope`} />
            <Stat k={type2 ? "Testing coverage" : "Evidence completeness"} v={`${(type2 ? k.testing_coverage_pct : k.evidence_completeness_pct) ?? 0}%`}
              n={type2 ? `${k.operating_effectiveness_pct ?? "—"}% operating effectively` : "controls with approved evidence"} />
            <Stat k="Open exceptions" v={k.open_exceptions} n={k.overdue_exceptions ? <span style={{ color: "var(--accent)" }}>{k.overdue_exceptions} past target</span> : `avg ${k.avg_days_open ?? 0} days open`} />
            <Stat k="High-risk controls failing" v={k.high_risk_ineffective} n={`${k.open_remediation} remediation tasks open`} />
          </div>
          <div className="grid g2">
            <div className="card"><h2>Control effectiveness</h2>
              <Donut data={x.status_distribution} center={<><div style={{ fontSize: 24, fontWeight: 700 }}>{x.controls.length}</div><div className="small muted">controls</div></>} />
            </div>
            <div className="card"><h2>Coverage by criterion</h2>
              <Radar axes={x.radar.map((r: any) => r.group)} series={[
                { name: "Implemented %", values: x.radar.map((r: any) => r.implemented_pct), color: "var(--series-1)" },
                ...(type2 || x.radar.some((r: any) => r.effective_pct) ? [{ name: "Effective %", values: x.radar.map((r: any) => r.effective_pct), color: "var(--series-2)" }] : []),
              ]} size={280} />
            </div>
          </div>
        </div>
      </div>

      <div className="grid g3" style={{ marginTop: 14 }}>
        <div className="card span2"><div className="spread"><h2>Controls implemented over time</h2><span className="small muted">% of scope, cumulative</span></div>
          <TrendLine max={100} points={x.trend.map((t: any) => ({ x: t.month, y: t.implemented_pct, extra: <>{t.evidence_approvals} evidence approvals · {t.tests} tests</> }))} />
        </div>
        <div className="card"><h2>Activity per month</h2>
          <p className="small muted" style={{ margin: "0 0 6px" }}>Evidence approvals</p>
          <Columns label="evidence approvals" color="var(--series-1)" points={x.trend.map((t: any) => ({ x: t.month, y: t.evidence_approvals }))} height={70} />
          <p className="small muted" style={{ margin: "10px 0 6px" }}>Control tests</p>
          <Columns label="tests" points={x.trend.map((t: any) => ({ x: t.month, y: t.tests }))} height={70} />
        </div>
      </div>

      <div className="grid g2" style={{ marginTop: 14 }}>
        <div className="card"><h2>Compliance matrix</h2><Matrix groups={Object.entries(groups).sort().map(([name, cs]) => ({ name, cells: cs }))} /></div>
        <div className="grid" style={{ alignContent: "start" }}>
          <div className="card"><h2>Implementation by domain</h2>
            <BarList rows={x.domains.map((r: any) => ({ label: r.domain, value: r.implemented_pct, note: `${r.implemented}/${r.controls} controls · ${r.open_exceptions} open exceptions` }))} />
          </div>
          <div className="card"><h2>Risk exposure</h2>
            <StackedBars rows={x.risk_exposure.map((r: any) => ({ label: r.risk, parts: Object.fromEntries(["effective", "needs_attention", "ineffective", "not_tested", "not_implemented"].map((s) => [s, r[s]])) }))} />
          </div>
        </div>
      </div>

      <div className="grid g3" style={{ marginTop: 14 }}>
        <div className="card span2"><h2>Top open exceptions</h2>
          {x.exceptions.top.length === 0 ? <p className="muted small">No open exceptions{type2 ? "" : " (exceptions come from Type II testing)"}.</p> :
            <table><thead><tr><th>ID</th><th>Control</th><th>Description</th><th>Severity</th><th>Days open</th><th>Owner</th></tr></thead>
              <tbody>{x.exceptions.top.map((e: any) => <tr key={e.code}><td className="mono">{e.code}</td><td className="mono">{e.control}</td>
                <td className="small">{e.description}<br /><span className="faint">{e.audit_impact}</span></td><td><Pill s={e.severity} /></td>
                <td>{e.days_open}{e.days_past_target ? <span style={{ color: "var(--accent)" }}> (+{e.days_past_target})</span> : null}</td><td className="small">{e.owner}</td></tr>)}</tbody></table>}
        </div>
        <div className="card"><h2>Milestones</h2>
          <div className="timeline">{x.milestones.map((m: any) => <div key={m.id} className={`ev ${m.status}`}>
            <div className="small"><b>{m.title}</b></div>
            <div className="faint small">{d(m.planned_date)} · {m.status === "done" ? `done ${d(m.completed_on)}` : m.status === "late" ? `${-m.days} days late` : `in ${m.days} days`}</div>
          </div>)}</div>
        </div>
      </div>

      <div className="grid g2" style={{ marginTop: 14 }}>
        <div className="card"><h2>Control owners by outstanding actions</h2>
          <table><thead><tr><th>Owner</th><th>Controls</th><th>Evidence gaps</th><th>Ineffective</th><th>Open exc.</th><th>Total open</th></tr></thead>
            <tbody>{x.owners.slice(0, 8).map((o: any) => <tr key={o.owner}><td>{o.owner}</td><td>{o.controls}</td><td>{o.evidence_gaps}</td><td>{o.ineffective}</td><td>{o.open_exceptions}</td><td><b>{o.total_open}</b></td></tr>)}</tbody></table>
        </div>
        <div className="card"><h2>Trust ledger</h2>
          <p className="muted small">{ledger.data?.statement}</p>
          {!ledger.data ? <Loading /> : ledger.data.signoffs.length === 0 ? <p className="muted small">No sign-offs yet.</p> :
            <table><tbody>{ledger.data.signoffs.slice(0, 6).map((s: any, i: number) => <tr key={i}>
              <td className="small">{dt(s.signed_at)}</td><td className="small">{s.subject_type.replace(/_/g, " ")}<br /><span className="faint">{s.signer}</span></td>
              <td className="mono faint">{s.content_sha256.slice(0, 12)}…</td></tr>)}</tbody></table>}
        </div>
      </div>
    </>
  );
}
