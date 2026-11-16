import { useState } from "react";
import { api, openAuthed, uploadEvidence } from "../api";
import AnalyticsOverview from "./Analytics";
import { Explorer, ImportCenter, Testing } from "./Assurance";
import Uploader from "./Uploader";
import { Err, Loading, Pill, Stat, Tabs, can, d, dt, go, money, pct, useAction, useApi, useMe, type Me } from "../ui";

export default function EngagementPage({ id, tab }: { id: string; tab?: string }) {
  const me = useMe();
  const e = useApi(`/v1/engagements/${id}`);
  if (e.error) return <Err e={e.error} />;
  if (!e.data) return <Loading />;
  const eng = e.data;
  const t = eng.tenant_id;
  const all: [string, string, boolean][] = [
    ["overview", "Dashboard", true],
    ["controls", "Controls explorer", true],
    ["testing", "Testing & exceptions", can(me, t, "remediation.read")],
    ["import", "Import centre", can(me, t, "evidence.upload")],
    ["evidence", "Evidence", can(me, t, "evidence.read_approved")],
    ["requests", "Requests", can(me, t, "request.read")],
    ["remediation", "Remediation", can(me, t, "remediation.read")],
    ["questionnaires", "Questionnaires", can(me, t, "questionnaire.read")],
    ["policies", "Policies", can(me, t, "policy.read")],
    ["reports", "Reports", true],
    ["connectors", "Connectors", can(me, t, "connector.read")],
    ["time", "Time & economics", can(me, t, "time.log")],
    ["audit", "Audit log", can(me, t, "audit.read")],
  ];
  const tabs = all.filter((x) => x[2]).map(([k, l]) => [k, l] as [string, string]);
  const on = tab && tabs.some(([k]) => k === tab) ? tab : "overview";
  const P = { eng, me, reload: e.reload };
  return (
    <>
      <div className="spread">
        <div>
          <p className="muted small" style={{ margin: 0 }}>{me.memberships.find((m) => m.tenant_id === t)?.tenant_name}</p>
          <h1>{eng.name}</h1>
          <div className="row small"><Pill s={eng.status} /><span>{eng.frameworks.join(" · ")}</span>
            <span className="muted">Target audit-ready {d(eng.target_date)}</span>
            {eng.signer && <span className="muted">Named signer: <b style={{ color: "var(--ink)" }}>{eng.signer.name}</b>, {eng.signer.title}</span>}</div>
        </div>
        <div className="row noprint">
          <a className="btn sm" href={`#/report/${eng.id}`}>Executive report</a>
          {can(me, t, "engagement.manage") && <StatusControl eng={eng} reload={e.reload} />}
        </div>
      </div>
      <Tabs tabs={tabs} on={on} set={(k) => go(`/engagements/${id}/${k}`)} />
      {on === "overview" && <AnalyticsOverview eng={eng} />}
      {on === "controls" && <Explorer {...P} />}
      {on === "testing" && <Testing {...P} />}
      {on === "import" && <ImportCenter {...P} />}
      {on === "evidence" && <EvidenceTab {...P} />}
      {on === "requests" && <Requests {...P} />}
      {on === "remediation" && <Remediation {...P} />}
      {on === "questionnaires" && <Questionnaires {...P} />}
      {on === "policies" && <Policies {...P} />}
      {on === "reports" && <Reports {...P} />}
      {on === "connectors" && <Connectors {...P} />}
      {on === "time" && <TimeTab {...P} />}
      {on === "audit" && <Audit {...P} />}
    </>
  );
}
type P = { eng: any; me: Me; reload: () => void };

const NEXT: Record<string, string[]> = {
  planning: ["in_progress"], in_progress: ["audit_ready", "on_hold"], on_hold: ["in_progress"], audit_ready: ["in_audit"],
  in_audit: ["maintaining"], maintaining: ["renewal_due"], renewal_due: ["maintaining"],
};
function StatusControl({ eng, reload }: { eng: any; reload: () => void }) {
  const { busy, run } = useAction();
  return <div className="row">{(NEXT[eng.status] || []).map((s) =>
    <button key={s} className="btn sm" disabled={busy} onClick={() => run(() => api.patch(`/v1/engagements/${eng.id}`, { version: eng.version, status: s }), `Moved to ${s.replace(/_/g, " ")}`, reload)}>
      → {s.replace(/_/g, " ")}</button>)}</div>;
}

function EvidenceTab({ eng, me }: P) {
  const ev = useApi<any[]>(`/v1/evidence?engagement_id=${eng.id}`);
  const canUp = can(me, eng.tenant_id, "evidence.upload");
  return (
    <>
      {canUp && <Uploader engagementId={eng.id} onChange={ev.reload} />}
      {!ev.data ? <Loading /> : <div className="card"><table>
        <thead><tr><th>Evidence</th><th>Status</th><th>Source</th><th>Period</th><th>SHA-256</th><th>Collected</th></tr></thead>
        <tbody>{ev.data.map((x) => <tr key={x.id} className="click" onClick={() => go(`/evidence/${x.id}`)}>
          <td><b>{x.title}</b> <span className="muted small">v{x.version}</span><br /><span className="small muted">{x.filename}</span>{x.error && <><br /><span className="small" style={{ color: "var(--accent)" }}>{x.error}</span></>}</td>
          <td><Pill s={x.status} /></td><td className="small">{x.source}</td>
          <td className="small">{x.period_start ? `${d(x.period_start)} → ${d(x.period_end)}` : "—"}</td>
          <td className="mono">{x.sha256 ? x.sha256.slice(0, 12) + "…" : "—"}</td><td className="small muted">{dt(x.collected_at)}</td>
        </tr>)}</tbody></table>{ev.data.length === 0 && <p className="muted">No evidence yet. Drop the demo evidence pack above to try it.</p>}</div>}
    </>
  );
}

function Requests({ eng, me }: P) {
  const r = useApi<any[]>(`/v1/engagements/${eng.id}/requests`);
  const { busy, run } = useAction();
  const [filter, setFilter] = useState("open");
  const canUp = can(me, eng.tenant_id, "evidence.upload");
  if (!r.data) return <Loading />;
  const rows = r.data.filter((x) => filter === "all" || x.status === filter);
  return <div className="card">
    <div className="spread"><h2>Evidence requests</h2><select style={{ width: 160 }} value={filter} onChange={(e) => setFilter(e.target.value)}>
      {["open", "submitted", "accepted", "all"].map((x) => <option key={x}>{x}</option>)}</select></div>
    <table><thead><tr><th>Request</th><th>Due</th><th>Status</th>{canUp && <th>Provide</th>}</tr></thead>
      <tbody>{rows.map((x) => <tr key={x.id}>
        <td><b>{x.title}</b><br /><span className="small muted">{x.instructions}</span></td>
        <td className="small" style={{ whiteSpace: "nowrap", color: x.status === "open" && x.due_date && new Date(x.due_date) < new Date() ? "var(--accent)" : undefined }}>{d(x.due_date)}</td>
        <td><Pill s={x.status} /></td>
        {canUp && <td>{["open", "submitted"].includes(x.status) && <input type="file" style={{ width: 200 }} disabled={busy}
          onChange={(e) => { const f = e.target.files?.[0]; if (f) run(() => uploadEvidence(eng.id, f, { requestId: x.id }), "Uploaded for this request", () => setTimeout(r.reload, 1200)); }} />}</td>}
      </tr>)}</tbody></table>{rows.length === 0 && <p className="muted">Nothing here.</p>}</div>;
}

const RNEXT: Record<string, string[]> = { open: ["in_progress"], in_progress: ["blocked", "ready_for_review"], blocked: ["in_progress"], ready_for_review: ["verified_closed", "in_progress"] };
function Remediation({ eng, me }: P) {
  const r = useApi<any[]>(`/v1/engagements/${eng.id}/remediation`);
  const { busy, run } = useAction();
  const [title, setTitle] = useState("");
  const staff = can(me, eng.tenant_id, "remediation.manage");
  const verify = can(me, eng.tenant_id, "remediation.verify");
  if (!r.data) return <Loading />;
  return <div className="card">
    <h2>Remediation</h2>
    {staff && <div className="row" style={{ marginBottom: 12 }}><input placeholder="New remediation task…" value={title} onChange={(e) => setTitle(e.target.value)} style={{ maxWidth: 480 }} />
      <button className="btn" disabled={busy || title.length < 3} onClick={() => run(() => api.post("/v1/remediation-tasks", { engagement_id: eng.id, title }), "Task created", () => { setTitle(""); r.reload(); })}>Add</button></div>}
    <table><thead><tr><th>Task</th><th>Severity</th><th>Due</th><th>Status</th><th></th></tr></thead>
      <tbody>{r.data.map((x) => <tr key={x.id}><td><b>{x.title}</b><br /><span className="small muted">{x.description}</span></td>
        <td><Pill s={x.severity} /></td><td className="small">{d(x.due_date)}</td><td><Pill s={x.status} /></td>
        <td><div className="row">{(RNEXT[x.status] || []).filter((s) => s !== "verified_closed" || verify).map((s) =>
          <button key={s} className={`btn sm ${s === "verified_closed" ? "ok" : ""}`} disabled={busy} onClick={() => run(() => api.patch(`/v1/remediation-tasks/${x.id}`, { version: x.version, status: s }), "Updated", r.reload)}>{s.replace(/_/g, " ")}</button>)}</div></td>
      </tr>)}</tbody></table>{r.data.length === 0 && <p className="muted">No remediation tasks.</p>}</div>;
}

function Questionnaires({ eng, me }: P) {
  const q = useApi<any[]>(`/v1/engagements/${eng.id}/questionnaires`);
  const { busy, run } = useAction();
  const [name, setName] = useState("");
  const [text, setText] = useState("");
  const staff = can(me, eng.tenant_id, "questionnaire.manage");
  return <>
    {staff && <div className="card" style={{ marginBottom: 14 }}>
      <h3>New security questionnaire</h3>
      <p className="muted small">Paste one question per line (or upload CSV/XLSX via the API). Answers are drafted only from <b>approved</b> evidence, cited, and need expert approval.</p>
      <div className="grid g2"><label className="f"><span>Name / requester</span><input value={name} onChange={(e) => setName(e.target.value)} placeholder="BigCo vendor review" /></label></div>
      <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder={"Is MFA enforced for all employees?\nDo you perform quarterly access reviews?"} />
      <div className="row" style={{ marginTop: 8 }}><button className="btn primary" disabled={busy || !name || !text.trim()} onClick={() => run(async () => {
        const r = await api.post(`/v1/engagements/${eng.id}/questionnaires`, { name, questions: text.split("\n").map((s) => s.trim()).filter(Boolean) });
        await api.post(`/v1/questionnaires/${r.id}/draft`); go(`/questionnaires/${r.id}`);
      }, "Questionnaire created; AI drafting queued")}>Create & draft answers</button></div>
    </div>}
    {!q.data ? <Loading /> : <div className="card"><table><thead><tr><th>Questionnaire</th><th>Status</th><th>Answers</th><th>Created</th></tr></thead>
      <tbody>{q.data.map((x) => <tr key={x.id} className="click" onClick={() => go(`/questionnaires/${x.id}`)}>
        <td><b>{x.name}</b><br /><span className="muted small">{x.requester}</span></td><td><Pill s={x.status} /></td>
        <td className="small">{Object.entries(x.counts).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(" · ")}</td><td className="small muted">{dt(x.created_at)}</td></tr>)}</tbody></table>
      {q.data.length === 0 && <p className="muted">No questionnaires.</p>}</div>}
  </>;
}

function Policies({ eng, me }: P) {
  const list = useApi<any[]>(`/v1/engagements/${eng.id}/policies`);
  const tpl = useApi<any[]>("/v1/policy-templates");
  const [sel, setSel] = useState<any>(null);
  const [key, setKey] = useState("");
  const [params, setParams] = useState<Record<string, string>>({});
  const { busy, run } = useAction();
  const staff = can(me, eng.tenant_id, "policy.manage");
  const clientApprove = can(me, eng.tenant_id, "policy.client_approve");
  const t = (tpl.data || []).find((x) => x.key === key);
  const open = (id: string) => api.get(`/v1/policies/${id}`).then(setSel);
  return <div className="split">
    <div>
      {staff && <div className="card" style={{ marginBottom: 14 }}>
        <h3>Draft a policy from a Tensor template</h3>
        <select value={key} onChange={(e) => { setKey(e.target.value); setParams({}); }}><option value="">Choose template…</option>
          {(tpl.data || []).map((x) => <option key={x.key} value={x.key}>{x.title}</option>)}</select>
        {t && <div className="grid g2" style={{ marginTop: 10 }}>{t.parameters.filter((p: string) => p !== "company_name").map((p: string) =>
          <label key={p} className="f"><span>{p.replace(/_/g, " ")}</span><input value={params[p] || ""} onChange={(e) => setParams({ ...params, [p]: e.target.value })} /></label>)}</div>}
        <button className="btn primary" disabled={!key || busy} onClick={() => run(async () => { const r = await api.post(`/v1/engagements/${eng.id}/policies`, { template_key: key, parameters: params }); setSel(r); }, "Draft created", list.reload)}>Generate draft</button>
      </div>}
      <div className="card"><table><thead><tr><th>Policy</th><th>Status</th><th>v</th></tr></thead>
        <tbody>{(list.data || []).map((x) => <tr key={x.id} className="click" onClick={() => open(x.id)}><td>{x.title}</td><td><Pill s={x.status} /></td><td>{x.version}</td></tr>)}</tbody></table>
        {list.data?.length === 0 && <p className="muted">No policies yet.</p>}</div>
    </div>
    <div>{sel && <div className="card">
      <div className="spread"><h3>{sel.title} <Pill s={sel.status} /></h3>
        <div className="row">
          {clientApprove && sel.status === "draft" && <button className="btn ok sm" disabled={busy} onClick={() => run(() => api.post(`/v1/policies/${sel.id}/client-approve`).then(setSel), "Approved", list.reload)}>Approve as client</button>}
          {staff && sel.status === "client_approved" && <button className="btn primary sm" disabled={busy} onClick={() => run(() => api.post(`/v1/policies/${sel.id}/publish`).then(setSel), "Published as evidence", list.reload)}>Publish as evidence</button>}
        </div></div>
      {staff && sel.status === "draft" ? <>
        <textarea style={{ minHeight: 420, fontFamily: "var(--mono)", fontSize: 12 }} value={sel.body_markdown} onChange={(e) => setSel({ ...sel, body_markdown: e.target.value })} />
        <button className="btn sm" style={{ marginTop: 8 }} disabled={busy} onClick={() => run(() => api.patch(`/v1/policies/${sel.id}`, { version: sel.version, body_markdown: sel.body_markdown }).then(setSel), "Saved")}>Save</button>
      </> : <pre style={{ whiteSpace: "pre-wrap", fontSize: 13 }}>{sel.body_markdown}</pre>}
    </div>}</div>
  </div>;
}

function Reports({ eng, me }: P) {
  const r = useApi<any[]>(`/v1/engagements/${eng.id}/reports`);
  const { busy, run } = useAction();
  const gen = can(me, eng.tenant_id, "report.generate");
  return <div className="card">
    <div className="spread"><h2>Deliverables</h2>
      {gen && <div className="row">{["evidence_index", "gap_report", "weekly_status"].map((k) =>
        <button key={k} className="btn sm" disabled={busy} onClick={() => run(() => api.post(`/v1/engagements/${eng.id}/reports`, { kind: k }), "Draft generated from approved records", r.reload)}>+ {k.replace(/_/g, " ")}</button>)}</div>}</div>
    <p className="muted small">Built only from evidence a named reviewer approved. Drafts need independent approval, and release needs a signer with MFA.</p>
    {!r.data ? <Loading /> : <table><thead><tr><th>Report</th><th>Status</th><th>SHA-256</th><th>Created</th><th></th></tr></thead>
      <tbody>{r.data.map((x) => <tr key={x.id}><td>{x.kind.replace(/_/g, " ")}</td><td><Pill s={x.status} /></td><td className="mono">{x.sha256.slice(0, 16)}…</td>
        <td className="small muted">{dt(x.created_at)}</td>
        <td><div className="row">
          <button className="btn sm" onClick={() => run(() => openAuthed(`/v1/reports/${x.id}`))}>View</button>
          {x.status === "draft" && can(me, eng.tenant_id, "report.approve") && <button className="btn sm" disabled={busy} onClick={() => run(() => api.post(`/v1/reports/${x.id}/approve`), "Approved", r.reload)}>Approve</button>}
          {x.status === "approved" && can(me, eng.tenant_id, "report.release") && <button className="btn ok sm" disabled={busy} onClick={() => run(() => api.post(`/v1/reports/${x.id}/release`), "Released to client", r.reload)}>Release</button>}
        </div></td></tr>)}</tbody></table>}
    {r.data?.length === 0 && <p className="muted">No reports yet.</p>}
  </div>;
}

function Connectors({ eng, me }: P) {
  const c = useApi<any[]>(`/v1/engagements/${eng.id}/connectors`);
  const { busy, run } = useAction();
  const [org, setOrg] = useState("");
  const manage = can(me, eng.tenant_id, "connector.manage");
  const approve = can(me, eng.tenant_id, "connector.customer_approve");
  return <div className="card">
    <h2>Read-only connectors</h2>
    <p className="muted small">Connectors only read settings and produce dated evidence snapshots. The client approves the read-only scope first. Tokens are encrypted and never shown again.</p>
    {manage && <div className="row" style={{ marginBottom: 12 }}><input style={{ maxWidth: 260 }} placeholder="GitHub organisation" value={org} onChange={(e) => setOrg(e.target.value)} />
      <button className="btn" disabled={busy || !org} onClick={() => run(() => api.post(`/v1/engagements/${eng.id}/connectors`, { provider: "github", config: { org } }), "Connector created", () => { setOrg(""); c.reload(); })}>Add GitHub</button></div>}
    {!c.data ? <Loading /> : <table><thead><tr><th>Provider</th><th>Status</th><th>Last sync</th><th></th></tr></thead>
      <tbody>{c.data.map((x) => <tr key={x.id}><td>{x.provider} · <span className="mono">{x.config.org}</span>{x.last_error && <><br /><span className="small" style={{ color: "var(--accent)" }}>{x.last_error}</span></>}</td>
        <td><Pill s={x.status} /> {!x.customer_approved && <span className="pill warn">awaiting client approval</span>}</td><td className="small">{dt(x.last_sync_at)}</td>
        <td><div className="row">
          {approve && !x.customer_approved && <button className="btn ok sm" disabled={busy} onClick={() => run(() => api.post(`/v1/connectors/${x.id}/customer-approve`), "Approved read-only access", c.reload)}>Approve read-only access</button>}
          {manage && x.customer_approved && x.status !== "revoked" && <button className="btn sm" disabled={busy} onClick={() => { const token = prompt("Paste a read-only GitHub token (fine-grained, Members + Administration: read)"); if (token) run(() => api.post(`/v1/connectors/${x.id}/authorize`, { token }), "Credential stored", c.reload); }}>Set token…</button>}
          {manage && ["active", "degraded"].includes(x.status) && <button className="btn sm" disabled={busy} onClick={() => run(() => api.post(`/v1/connectors/${x.id}/sync`), "Sync queued", c.reload)}>Sync now</button>}
          {(manage || approve) && x.status !== "revoked" && <button className="btn danger sm" disabled={busy} onClick={() => run(() => api.post(`/v1/connectors/${x.id}/revoke`), "Revoked and credential destroyed", c.reload)}>Revoke</button>}
        </div></td></tr>)}</tbody></table>}
  </div>;
}

function TimeTab({ eng, me }: P) {
  const m = useApi(can(me, eng.tenant_id, "metrics.read") ? `/v1/engagements/${eng.id}/metrics` : null);
  const te = useApi<any[]>(`/v1/engagements/${eng.id}/time-entries`);
  const ctl = useApi<any[]>(`/v1/engagements/${eng.id}/controls`);
  const { busy, run } = useAction();
  const [f, setF] = useState({ minutes: "60", activity: "evidence_collection", ai: true, ec: "", note: "" });
  const x = m.data;
  return <>
    {x && <div className="grid g4" style={{ marginBottom: 14 }}>
      <Stat k="Hours logged" v={x.hours_total} n={`targets ${x.target_hours_year1} h (Y1) · ${x.target_hours_year2} h (Y2)`} />
      <Stat k="AI-assisted hours" v={x.hours_ai_assisted} n={`${x.ai_reviews} AI proposals reviewed`} />
      <Stat k="AI acceptance / edit / reject" v={pct(x.ai_acceptance_rate)} n={`${pct(x.ai_edit_rate)} edited · ${pct(x.ai_reject_rate)} rejected`} />
      <Stat k="Contribution" v={money(x.contribution_usd)} n={`labour ${money(x.labour_cost_usd)} · AI ${money(x.ai_cost_usd)}`} />
    </div>}
    <div className="split">
      <div className="card"><h3>Log time (maps year-1 effort per control)</h3>
        <div className="grid g2">
          <label className="f"><span>Minutes</span><input value={f.minutes} onChange={(e) => setF({ ...f, minutes: e.target.value })} /></label>
          <label className="f"><span>Activity</span><select value={f.activity} onChange={(e) => setF({ ...f, activity: e.target.value })}>
            {["evidence_collection", "review", "policy", "questionnaire", "signoff", "meeting", "remediation", "other"].map((a) => <option key={a}>{a}</option>)}</select></label>
        </div>
        <label className="f"><span>Control (optional)</span><select value={f.ec} onChange={(e) => setF({ ...f, ec: e.target.value })}><option value="">—</option>
          {(ctl.data || []).map((c) => <option key={c.id} value={c.id}>{c.code} {c.title}</option>)}</select></label>
        <label className="row small" style={{ gap: 6, marginBottom: 10 }}><input type="checkbox" style={{ width: "auto" }} checked={f.ai} onChange={(e) => setF({ ...f, ai: e.target.checked })} />AI-assisted</label>
        <button className="btn primary" disabled={busy} onClick={() => run(() => api.post("/v1/time-entries", { engagement_id: eng.id, minutes: Number(f.minutes), activity: f.activity, ai_assisted: f.ai, engagement_control_id: f.ec || null }), "Logged", () => { te.reload(); m.reload(); })}>Log</button>
      </div>
      <div className="card"><h3>Entries</h3><table><tbody>{(te.data || []).slice(0, 30).map((t) => <tr key={t.id}>
        <td className="small">{t.work_date}</td><td>{t.user}<br /><span className="small muted">{t.role_class}</span></td><td>{t.activity.replace(/_/g, " ")}</td><td>{t.minutes} min</td><td>{t.ai_assisted ? <span className="pill info">AI</span> : ""}</td></tr>)}</tbody></table></div>
    </div>
    {x && <div className="card" style={{ marginTop: 14 }}><h3>Estimate vs actual by control</h3><table><thead><tr><th>Control</th><th>Estimate (h)</th><th>Actual (h)</th></tr></thead>
      <tbody>{x.hours_by_control.filter((r: any) => r.actual_h > 0).map((r: any) => <tr key={r.control}><td className="mono">{r.control}</td><td>{r.estimate_h}</td><td>{r.actual_h}</td></tr>)}</tbody></table></div>}
  </>;
}

function Audit({ eng }: P) {
  const a = useApi<any[]>(`/v1/audit-events?tenant_id=${eng.tenant_id}&limit=300`);
  if (!a.data) return <Loading />;
  return <div className="card"><p className="muted small">Append-only. The database rejects updates and deletes to this log.</p><table>
    <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Target</th><th>Outcome</th></tr></thead>
    <tbody>{a.data.map((x) => <tr key={x.id}><td className="small">{dt(x.at)}</td><td>{x.actor}</td><td className="mono">{x.action}</td>
      <td className="small muted">{x.target_type} {x.target_id?.slice(0, 8)}</td><td><Pill s={x.outcome === "success" ? "succeeded" : x.outcome} /></td></tr>)}</tbody></table></div>;
}
