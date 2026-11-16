import { useEffect, useState } from "react";
import { api, fetchHtml } from "../api";
import { Err, Loading, Pill, dt, go, money, useAction, useApi, useMe } from "../ui";

export function SnapshotList() {
  const s = useApi<any[]>("/v1/snapshots");
  return (
    <>
      <div className="spread"><div><h1>Readiness snapshots</h1><p className="sub">The free first step on every channel: gaps, a dated plan, an all-in quote and the signer's name, within 3 business days.</p></div>
        <button className="btn primary" onClick={() => go("/snapshots/new")}>New snapshot</button></div>
      <Err e={s.error} />
      {!s.data ? <Loading /> : <div className="card"><table>
        <thead><tr><th>Prospect</th><th>Frameworks</th><th>Readiness</th><th>Quote (Tensor, yr 1)</th><th>Status</th><th>Due (SLA)</th></tr></thead>
        <tbody>{s.data.map((x) => {
          const late = ["drafted", "intake"].includes(x.status) && x.due_at && new Date(x.due_at) < new Date();
          return <tr key={x.id} className="click" onClick={() => go(`/snapshots/${x.id}`)}>
            <td><b>{x.company}</b><br /><span className="muted small">{x.contact_name} · {x.contact_email}</span></td><td>{x.frameworks.join(", ")}</td>
            <td>{x.result?.readiness_percent}%</td><td>{money(x.result?.quote?.tensor_total_year1)}{x.founding_client && <> <span className="pill info">founding</span></>}</td>
            <td><Pill s={x.status} /></td><td className="small" style={{ color: late ? "var(--accent)" : undefined }}>{dt(x.due_at)}</td></tr>;
        })}</tbody></table>{s.data.length === 0 && <p className="muted">No snapshots yet.</p>}</div>}
    </>
  );
}

export function SnapshotNew() {
  const staff = useApi<any[]>("/v1/staff");
  const partners = useApi<any[]>("/v1/partners");
  const [fw, setFw] = useState<string[]>(["SOC2"]);
  const qs = useApi<any[]>(`/v1/snapshot-questions?frameworks=${fw.join(",")}`, [fw.join(",")]);
  const [f, setF] = useState({ company_name: "", contact_name: "", contact_email: "", employee_count: "25", industry: "B2B SaaS", signer_id: "", partner_id: "", founding_client: false, pentest: false });
  const [ans, setAns] = useState<Record<string, string>>({});
  const { busy, run } = useAction();
  const set = (k: string, v: any) => setF({ ...f, [k]: v });
  const submit = () => run(async () => {
    const r = await api.post("/v1/snapshots", { ...f, employee_count: Number(f.employee_count), frameworks: fw, intake: ans, partner_id: f.partner_id || null });
    go(`/snapshots/${r.id}`);
  }, "Snapshot drafted. Send it to the named signer for review.");
  return (
    <>
      <h1>New readiness snapshot</h1>
      <p className="sub">Fill this in during the discovery call. Scoring is deterministic and traceable to these answers.</p>
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="grid g3">
          <label className="f"><span>Company</span><input value={f.company_name} onChange={(e) => set("company_name", e.target.value)} /></label>
          <label className="f"><span>Contact name</span><input value={f.contact_name} onChange={(e) => set("contact_name", e.target.value)} /></label>
          <label className="f"><span>Contact email</span><input type="email" value={f.contact_email} onChange={(e) => set("contact_email", e.target.value)} /></label>
          <label className="f"><span>Employees</span><input value={f.employee_count} onChange={(e) => set("employee_count", e.target.value)} /></label>
          <label className="f"><span>Named signer</span><select value={f.signer_id} onChange={(e) => set("signer_id", e.target.value)}><option value="">Choose…</option>
            {(staff.data || []).filter((s) => s.can_sign).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
          <label className="f"><span>White-label partner</span><select value={f.partner_id} onChange={(e) => set("partner_id", e.target.value)}><option value="">— direct —</option>
            {(partners.data || []).map((p) => <option key={p.id} value={p.id}>{p.brand_name}</option>)}</select></label>
        </div>
        <div className="row">
          {["SOC2", "ISO27001", "ISO42001", "PCIDSS"].map((x) => <label key={x} className="row small" style={{ gap: 4 }}><input type="checkbox" style={{ width: "auto" }} checked={fw.includes(x)}
            onChange={(e) => setFw(e.target.checked ? [...fw, x] : fw.filter((y) => y !== x))} />{x}</label>)}
          <label className="row small" style={{ gap: 4 }}><input type="checkbox" style={{ width: "auto" }} checked={f.founding_client} onChange={(e) => set("founding_client", e.target.checked)} />Founding client ($5,000)</label>
          <label className="row small" style={{ gap: 4 }}><input type="checkbox" style={{ width: "auto" }} checked={f.pentest} onChange={(e) => set("pentest", e.target.checked)} />Add pentest</label>
        </div>
      </div>
      <div className="card">
        <h2>Intake questions</h2>
        {!qs.data ? <Loading /> : <table><tbody>{qs.data.map((q) => <tr key={q.key}>
          <td><span className="pill">{q.domain}</span> {q.question}</td>
          <td style={{ whiteSpace: "nowrap" }}>{["yes", "partial", "no", "unknown"].map((a) =>
            <label key={a} className="small" style={{ marginRight: 10 }}><input type="radio" style={{ width: "auto" }} name={q.key} checked={(ans[q.key] || "unknown") === a} onChange={() => setAns({ ...ans, [q.key]: a })} /> {a}</label>)}</td>
        </tr>)}</tbody></table>}
        <button className="btn primary" style={{ marginTop: 12 }} disabled={busy || !f.company_name || !f.contact_email || !f.signer_id || !fw.length} onClick={submit}>Draft snapshot</button>
      </div>
    </>
  );
}

export function SnapshotDetail({ id }: { id: string }) {
  const me = useMe();
  const s = useApi(`/v1/snapshots/${id}`);
  const [html, setHtml] = useState("");
  const { busy, run } = useAction();
  useEffect(() => { fetchHtml(`/v1/snapshots/${id}/document`).then(setHtml).catch(() => setHtml("")); }, [id, s.data?.status]);
  if (s.error) return <Err e={s.error} />;
  if (!s.data) return <Loading />;
  const x = s.data;
  return (
    <>
      <p className="small" style={{ margin: 0 }}><a href="#/snapshots">← Snapshots</a></p>
      <div className="spread"><div><h1>{x.result?.company}</h1><div className="row small"><Pill s={x.status} /><span className="muted">due {dt(x.due_at)}</span>
        {x.signoff && <span className="muted">signed by {x.signoff.signer} · <span className="mono">{x.signoff.sha256.slice(0, 12)}…</span></span>}</div></div>
        <div className="row">
          {x.status === "drafted" && x.signer_id === me.id && <button className="btn ok" disabled={busy} onClick={() => run(() => api.post(`/v1/snapshots/${id}/sign`), "Signed", s.reload)}>Sign as {me.name}</button>}
          {x.status === "drafted" && x.signer_id !== me.id && <span className="note">Waiting for the named signer</span>}
          {x.status === "signed" && <button className="btn" disabled={busy} onClick={() => run(() => api.post(`/v1/snapshots/${id}/status`, { status: "sent" }), "Marked as sent", s.reload)}>Mark sent</button>}
          {["signed", "sent"].includes(x.status) && <button className="btn primary" disabled={busy} onClick={() => run(async () => { const r = await api.post(`/v1/snapshots/${id}/convert`); go(`/engagements/${r.engagement_id}`); }, "Won! Engagement opened")}>Client signed → open engagement</button>}
          {["drafted", "signed", "sent"].includes(x.status) && <button className="btn danger" disabled={busy} onClick={() => run(() => api.post(`/v1/snapshots/${id}/status`, { status: "lost" }), "Marked lost", s.reload)}>Lost</button>}
        </div></div>
      <iframe className="doc" title="Snapshot document" sandbox="" srcDoc={html} style={{ marginTop: 14 }} />
    </>
  );
}
