import { Components, Donut, Gauge, Radar } from "../charts";
import { Err, Loading, Pill, d, useApi } from "../ui";

export default function ExecutiveReport({ id }: { id: string }) {
  const r = useApi(`/v1/engagements/${id}/executive-summary`);
  if (r.error) return <Err e={r.error} />;
  if (!r.data) return <Loading what="Preparing report" />;
  const x = r.data, a = x.analytics, k = a.kpis;
  const sections: Record<string, { total: number; impl: number; eff: number; inef: number }> = {};
  a.radar.forEach((g: any) => { sections[g.group] = { total: g.controls, impl: Math.round((g.implemented_pct || 0) * g.controls / 100), eff: Math.round((g.effective_pct || 0) * g.controls / 100), inef: 0 }; });
  a.controls.forEach((c: any) => c.status === "ineffective" && c.groups.forEach((g: string) => sections[g] && sections[g].inef++));
  return (
    <>
      <div className="spread noprint" style={{ marginBottom: 10 }}>
        <a href={`#/engagements/${id}`} className="small">← Back to engagement</a>
        <button className="btn primary" onClick={() => window.print()}>Print / Save as PDF</button>
      </div>
      <div className="card">
        <div className="spread">
          <div>
            <p className="faint small" style={{ margin: 0 }}>Executive readiness report · as of {a.as_of}</p>
            <h1>{x.engagement}</h1>
            <p className="muted" style={{ margin: 0 }}>{x.frameworks.join(" · ")} · target audit-ready {d(x.target_date)}{x.signer ? ` · named signer ${x.signer.name}, ${x.signer.title}` : ""}</p>
          </div>
          <div style={{ width: 200 }}><Gauge score={a.readiness.score} rating={a.readiness.rating} size={170} /></div>
        </div>
      </div>
      <div className="grid g2" style={{ marginTop: 14 }}>
        <div className="card"><h2>Key findings</h2><ul style={{ margin: 0, paddingLeft: 18 }}>{x.findings.map((f: string, i: number) => <li key={i} style={{ marginBottom: 6 }}>{f}</li>)}</ul>
          <p className="faint small">{x.basis}</p></div>
        <div className="card"><h2>Score components</h2><Components items={a.readiness.components} />
          <div className="grid g3" style={{ marginTop: 10 }}>
            <div><div className="faint small">Implemented</div><b>{k.controls_implemented}/{k.controls_in_scope}</b></div>
            <div><div className="faint small">Open exceptions</div><b>{k.open_exceptions}</b></div>
            <div><div className="faint small">Open remediation</div><b>{k.open_remediation}</b></div>
          </div></div>
      </div>
      <div className="grid g2" style={{ marginTop: 14 }}>
        <div className="card"><h2>Control effectiveness</h2><Donut data={a.status_distribution} /></div>
        <div className="card"><h2>Coverage by criterion</h2><Radar axes={a.radar.map((g: any) => g.group)} series={[{ name: "Implemented %", values: a.radar.map((g: any) => g.implemented_pct), color: "var(--series-1)" }]} size={280} /></div>
      </div>
      <div className="card" style={{ marginTop: 14 }}><h2>Compliance by criterion</h2>
        <table><thead><tr><th>Criterion / theme</th><th>Controls</th><th>Implemented</th><th>Effective</th><th>Ineffective</th><th>Implemented %</th><th>Status</th></tr></thead>
          <tbody>{a.radar.map((g: any) => <tr key={g.group}><td>{g.group}</td><td>{g.controls}</td><td>{sections[g.group].impl}</td><td>{sections[g.group].eff}</td>
            <td>{sections[g.group].inef}</td><td>{g.implemented_pct ?? 0}%</td><td><Pill s={g.status === "Ready" ? "approved" : g.status === "At risk" ? "partial" : "failed"} /> <span className="small">{g.status}</span></td></tr>)}</tbody></table></div>
      <div className="card" style={{ marginTop: 14 }}><h2>Controls needing attention</h2>
        <table><thead><tr><th>Control</th><th>Title</th><th>Status</th><th>Owner</th><th>Risk</th></tr></thead>
          <tbody>{a.controls.filter((c: any) => ["ineffective", "not_implemented", "not_tested", "needs_attention"].includes(c.status)).slice(0, 25).map((c: any) =>
            <tr key={c.id}><td className="mono">{c.code}</td><td className="small">{c.title}</td><td className="small">{c.status.replace(/_/g, " ")}</td><td className="small">{c.owner}</td><td className="small">{c.risk}</td></tr>)}</tbody></table>
        <p className="faint small">Confidential: for internal governance use. Readiness deliverable prepared by TensorInfoSec; not an audit opinion. An independent audit firm issues the SOC 2 report or ISO certificate.</p>
      </div>
    </>
  );
}
