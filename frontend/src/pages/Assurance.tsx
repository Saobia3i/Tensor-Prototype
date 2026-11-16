import { Fragment, useMemo, useRef, useState } from "react";
import { api, openAuthed } from "../api";
import { STATUS_META } from "../charts";
import { Err, Loading, Pill, can, d, useAction, useApi, type Me } from "../ui";

type P = { eng: any; me: Me; reload: () => void };

/* ---------------- Controls explorer (filters, effectiveness, owner/risk, workflow actions, CSV) */
export function Explorer({ eng, me }: P) {
  const a = useApi(`/v1/engagements/${eng.id}/analytics`);
  const wf = useApi<any[]>(`/v1/engagements/${eng.id}/controls`);
  const [q, setQ] = useState(""); const [dom, setDom] = useState(""); const [st, setSt] = useState(""); const [grp, setGrp] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const { busy, run } = useAction();
  const assess = can(me, eng.tenant_id, "control.assess");
  const sign = can(me, eng.tenant_id, "signoff.create") && me.can_sign;
  const reload = () => { a.reload(); wf.reload(); };
  const byId = useMemo(() => Object.fromEntries((wf.data || []).map((c) => [c.id, c])), [wf.data]);
  if (a.error) return <Err e={a.error} />;
  if (!a.data || !wf.data) return <Loading />;
  const rows = a.data.controls.filter((c: any) =>
    (!q || `${c.code} ${c.title} ${c.refs.join(" ")} ${c.owner}`.toLowerCase().includes(q.toLowerCase())) &&
    (!dom || c.domain === dom) && (!st || c.status === st) && (!grp || c.groups.includes(grp)));
  const domains = [...new Set(a.data.controls.map((c: any) => c.domain))].sort() as string[];
  const groups = [...new Set(a.data.controls.flatMap((c: any) => c.groups))].sort() as string[];
  const patch = (id: string, body: any) => run(() => api.patch(`/v1/engagement-controls/${id}`, { version: byId[id].version, ...body }), "Saved", reload);
  return (
    <div className="card">
      <div className="row noprint" style={{ marginBottom: 12 }}>
        <input placeholder="Search code, title, framework ref, owner…" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 300 }} />
        <select value={grp} onChange={(e) => setGrp(e.target.value)} style={{ width: 190 }}><option value="">All criteria / themes</option>{groups.map((g) => <option key={g}>{g}</option>)}</select>
        <select value={dom} onChange={(e) => setDom(e.target.value)} style={{ width: 160 }}><option value="">All domains</option>{domains.map((g) => <option key={g}>{g}</option>)}</select>
        <select value={st} onChange={(e) => setSt(e.target.value)} style={{ width: 170 }}><option value="">All statuses</option>
          {Object.entries(STATUS_META).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select>
        <span className="small muted">{rows.length} of {a.data.controls.length}</span>
        <button className="btn sm" style={{ marginLeft: "auto" }} onClick={() => run(() => openAuthed(`/v1/engagements/${eng.id}/controls.csv`))}>Export CSV</button>
      </div>
      <table>
        <thead><tr><th>Control</th><th>Refs</th><th>Effectiveness</th><th>Workflow</th><th>Owner · risk</th><th>Self-assessed</th><th>Evidence</th><th>Last test</th></tr></thead>
        <tbody>{rows.map((c: any) => <Fragment key={c.id}>
          <tr className="click" onClick={() => setOpen(open === c.id ? null : c.id)}>
            <td><span className="mono">{c.code}</span><br /><b className="small">{c.title}</b></td>
            <td className="small faint" style={{ maxWidth: 180 }}>{c.refs.slice(0, 4).join(", ")}{c.refs.length > 4 ? "…" : ""}</td>
            <td><span className="pill" style={{ color: "var(--ink)" }}><i style={{ width: 8, height: 8, borderRadius: 4, background: STATUS_META[c.status].color }} />{STATUS_META[c.status].label}</span></td>
            <td><Pill s={c.workflow_status} /></td>
            <td className="small">{c.owner}<br /><Pill s={c.risk} /></td>
            <td>{c.self_assessment ? <span className="pill">{c.self_assessment}</span> : <span className="faint">—</span>}</td>
            <td>{c.approved_evidence}{c.signed_off ? <> · <span className="pill ok">signed</span></> : null}</td>
            <td className="small">{c.last_test ? <><Pill s={c.last_test.result === "pass" ? "approved" : c.last_test.result === "fail" ? "failed" : "partial"} /><br /><span className="faint">{c.last_test.deviations}/{c.last_test.samples} · {d(c.last_test.date)}</span></> : <span className="faint">—</span>}</td>
          </tr>
          {open === c.id && byId[c.id] && <tr><td colSpan={8} style={{ background: "var(--soft)" }}>
            <p style={{ margin: "0 0 6px" }}><b>Objective.</b> {byId[c.id].objective}</p>
            <p className="small muted" style={{ margin: "0 0 6px" }}><b>Guidance.</b> {byId[c.id].guidance}</p>
            <p className="small" style={{ margin: "0 0 10px" }}><b>Typical evidence:</b> {byId[c.id].evidence_examples.join("; ")}</p>
            {assess && <div className="row" style={{ marginBottom: 8 }}>
              <input defaultValue={c.owner === "Unassigned" ? "" : c.owner} placeholder="Control owner" style={{ maxWidth: 220 }} onBlur={(e) => e.target.value !== (c.owner === "Unassigned" ? "" : c.owner) && patch(c.id, { owner_name: e.target.value })} />
              <select defaultValue={c.risk} style={{ width: 130 }} onChange={(e) => patch(c.id, { risk_rating: e.target.value })}>{["high", "medium", "low"].map((r) => <option key={r}>{r}</option>)}</select>
            </div>}
            <div className="row">
              {assess && c.workflow_status === "not_started" && <button className="btn sm" disabled={busy} onClick={() => patch(c.id, { status: "in_progress" })}>Start</button>}
              {assess && c.workflow_status === "evidence_collected" && <button className="btn sm" disabled={busy} onClick={() => patch(c.id, { status: "ready_for_signoff" })}>Mark ready for sign-off</button>}
              {sign && c.workflow_status === "ready_for_signoff" && <button className="btn ok sm" disabled={busy} onClick={() => run(() => api.post(`/v1/engagement-controls/${c.id}/signoff`, {}), "Signed off (hash recorded)", reload)}>Sign off as {me.name}</button>}
              {assess && ["not_started", "in_progress"].includes(c.workflow_status) && <button className="btn sm" disabled={busy} onClick={() => {
                const why = prompt("Why is this control not applicable? (recorded for the auditor)");
                if (why) patch(c.id, { status: "not_applicable", applicability_rationale: why });
              }}>Not applicable…</button>}
            </div>
          </td></tr>}
        </Fragment>)}</tbody>
      </table>
    </div>
  );
}

/* ---------------- Type II testing + exceptions */
export function Testing({ eng, me }: P) {
  const tests = useApi<any[]>(`/v1/engagements/${eng.id}/tests`);
  const exc = useApi<any[]>(`/v1/engagements/${eng.id}/exceptions`);
  const ctl = useApi<any[]>(`/v1/engagements/${eng.id}/controls`);
  const { busy, run } = useAction();
  const assess = can(me, eng.tenant_id, "control.assess");
  const verify = can(me, eng.tenant_id, "remediation.verify");
  const [f, setF] = useState({ ec: "", date: new Date().toISOString().slice(0, 10), samples: "25", deviations: "0", procedure: "" });
  const reload = () => { tests.reload(); exc.reload(); };
  return (
    <div className="grid">
      {assess && <div className="card">
        <h2>Record an operating-effectiveness test</h2>
        <div className="grid g6" style={{ alignItems: "end" }}>
          <label className="f span2"><span>Control</span><select value={f.ec} onChange={(e) => setF({ ...f, ec: e.target.value })}><option value="">Choose…</option>
            {(ctl.data || []).map((c) => <option key={c.id} value={c.id}>{c.code} · {c.title}</option>)}</select></label>
          <label className="f"><span>Test date</span><input type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} /></label>
          <label className="f"><span>Samples tested</span><input value={f.samples} onChange={(e) => setF({ ...f, samples: e.target.value })} /></label>
          <label className="f"><span>Deviations</span><input value={f.deviations} onChange={(e) => setF({ ...f, deviations: e.target.value })} /></label>
          <label className="f"><span>&nbsp;</span><button className="btn primary" style={{ width: "100%" }} disabled={busy || !f.ec} onClick={() => run(async () => {
            const r = await api.post(`/v1/engagements/${eng.id}/tests`, { engagement_control_id: f.ec, test_date: f.date, samples_tested: Number(f.samples), deviations: Number(f.deviations), procedure: f.procedure });
            return r;
          }, "Test recorded (exceptions raised automatically on deviations)", reload)}>Record</button></label>
        </div>
        <input placeholder="Test procedure (e.g. sample 25 merged PRs for an approval)" value={f.procedure} onChange={(e) => setF({ ...f, procedure: e.target.value })} />
        <p className="faint small" style={{ marginBottom: 0 }}>Result is computed: 0 deviations = pass, ≤ 10% = pass with exception, otherwise fail. Or bulk-load a test log in the Import centre.</p>
      </div>}
      <div className="card"><h2>Exceptions register</h2>
        {!exc.data ? <Loading /> : exc.data.length === 0 ? <p className="muted small">No exceptions.</p> :
          <table><thead><tr><th>ID</th><th>Description</th><th>Severity</th><th>Status</th><th>Opened</th><th>Target</th><th>Days open</th><th>Owner</th><th></th></tr></thead>
            <tbody>{exc.data.map((x) => <tr key={x.id}><td className="mono">{x.code}</td><td className="small" style={{ maxWidth: 360 }}>{x.description}<br /><span className="faint">{x.audit_impact}</span></td>
              <td><Pill s={x.severity} /></td><td><Pill s={x.status === "open" ? (x.overdue ? "error" : "open") : "approved"} /> <span className="small faint">{x.status.replace(/_/g, " ")}</span></td>
              <td className="small">{d(x.opened_on)}</td><td className="small" style={{ color: x.overdue ? "var(--accent)" : undefined }}>{d(x.target_date)}</td><td>{x.days_open}</td><td className="small">{x.owner}</td>
              <td>{verify && x.status === "open" && <button className="btn sm" disabled={busy} onClick={() => { const c = prompt("Closure comment (retest result)?"); if (c) run(() => api.patch(`/v1/exceptions/${x.id}`, { status: "closed", comment: c }), "Exception closed", reload); }}>Close…</button>}</td>
            </tr>)}</tbody></table>}
      </div>
      <div className="card"><h2>Test log</h2>
        {!tests.data ? <Loading /> : tests.data.length === 0 ? <p className="muted small">No tests recorded yet. Engagements without tests use the readiness (Type I) score.</p> :
          <table><thead><tr><th>Date</th><th>Control</th><th>Procedure</th><th>Samples</th><th>Deviations</th><th>Result</th></tr></thead>
            <tbody>{tests.data.map((t) => <tr key={t.id}><td className="small">{d(t.test_date)}</td><td><span className="mono">{t.control}</span><br /><span className="small faint">{t.title}</span></td>
              <td className="small">{t.procedure}</td><td>{t.samples}{t.population ? <span className="faint"> / {t.population}</span> : null}</td><td>{t.deviations}</td>
              <td><Pill s={t.result === "pass" ? "approved" : t.result === "fail" ? "failed" : "partial"} /> <span className="small faint">{t.result.replace(/_/g, " ")}</span></td></tr>)}</tbody></table>}
      </div>
    </div>
  );
}

/* ---------------- Import centre */
export function ImportCenter({ eng, me, reload }: P) {
  const [kind, setKind] = useState<"assessment" | "tests">("assessment");
  const [file, setFile] = useState<File | null>(null);
  const [log, setLog] = useState<any>(null);
  const [over, setOver] = useState(false);
  const ref = useRef<HTMLInputElement>(null);
  const { busy, run } = useAction();
  const canTests = can(me, eng.tenant_id, "control.assess");
  const send = (apply: boolean) => run(async () => {
    const fd = new FormData(); fd.append("file", file!);
    setLog(await api.form(`/v1/engagements/${eng.id}/import?kind=${kind}&apply=${apply}`, fd));
  }, apply ? "Imported" : undefined, apply ? reload : undefined);
  return (
    <div className="grid g2" style={{ alignItems: "start" }}>
      <div className="card">
        <h2>Import centre</h2>
        <p className="muted small">Upload a gap-assessment or a SOC 2 test log (.xlsx or .csv). Headers are matched flexibly (“Standard Ref”, “TSC Ref”, “Control ID”…), the right sheet is found automatically, and every row is validated before anything is saved.</p>
        <div className="tabs" style={{ margin: "8px 0 12px" }}>
          <button className={kind === "assessment" ? "on" : ""} onClick={() => { setKind("assessment"); setLog(null); }}>Gap assessment</button>
          {canTests && <button className={kind === "tests" ? "on" : ""} onClick={() => { setKind("tests"); setLog(null); }}>Type II test log</button>}
        </div>
        <div className={`drop ${over ? "over" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) { setFile(f); setLog(null); } }} onClick={() => ref.current?.click()} style={{ cursor: "pointer" }}>
          <div style={{ fontSize: 15, fontWeight: 600 }}>{file ? file.name : "Drop a spreadsheet here"}</div>
          <div className="small muted">{file ? `${Math.round(file.size / 1024)} KB` : "or click to browse · .xlsx, .csv · max 10 MB"}</div>
          <input ref={ref} type="file" accept=".xlsx,.csv" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) { setFile(f); setLog(null); } }} />
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn" disabled={!file || busy} onClick={() => send(false)}>Validate (dry run)</button>
          <button className="btn primary" disabled={!file || busy || !log || log.errors || log.applied} onClick={() => send(true)}>Apply import</button>
        </div>
        <div className="note" style={{ marginTop: 12 }}>
          {kind === "assessment" ? "A self-assessment is recorded next to each control. It never marks a control implemented: that needs approved evidence and an expert. Tensor staff imports create remediation tasks for gaps."
            : "Each row becomes a test. Deviations automatically raise exceptions (EXC-###) with a linked remediation task; severity follows the control's risk rating."}
        </div>
        <p className="small faint">Expected columns: {kind === "assessment" ? "Standard Ref · Compliance (Fully / Partially / Not Compliant / Not Applicable) · optional Owner, Priority, Notes, Evidence" : "Control Ref · Test Date · Samples Tested · Deviations · optional Result, Population, Test Procedure, Owner, Notes"}</p>
      </div>
      <div className="card">
        <h2>Validation log</h2>
        {!log ? <p className="muted small">Run a dry run to see what will be imported.</p> : <>
          {log.errors && <div className="err">{log.errors.join(" ")}</div>}
          <div className="grid g3" style={{ marginBottom: 10 }}>
            <div><div className="faint small">Sheet</div><b>{log.sheet}</b> <span className="faint small">header row {log.header_row}</span></div>
            <div><div className="faint small">Rows used / read</div><b>{log.rows_used} / {log.rows_read}</b></div>
            <div><div className="faint small">Controls affected</div><b>{log.controls_affected ?? 0}</b></div>
          </div>
          {log.applied && <div className="note" style={{ marginBottom: 10 }}>Applied. {kind === "assessment" ? `${log.remediation_tasks_created} remediation tasks created.` : `${log.tests_created} tests and ${log.exceptions_created} exceptions created.`}</div>}
          <p className="small"><b>Columns matched:</b> {Object.entries(log.columns || {}).map(([k, v]) => `${k} → col ${v}`).join(" · ")}</p>
          {log.duplicates?.length > 0 && <div className="warnbox">Duplicate references: {log.duplicates.join(", ")}</div>}
          {log.unmatched?.length > 0 && <div className="warnbox">Not matched to an in-scope control: {log.unmatched.join(", ")}</div>}
          {log.invalid?.length > 0 && <div className="warnbox">{log.invalid.map((i: any) => `Row ${i.row}: ${i.issue}`).join(" · ")}</div>}
          {log.skipped_not_assessed > 0 && <p className="small muted">{log.skipped_not_assessed} row(s) not assessed, skipped.</p>}
          {log.warnings?.map((w: string, i: number) => <p key={i} className="small muted">{w}</p>)}
          {log.preview?.length > 0 && <table><thead><tr>{kind === "assessment" ? <><th>Control</th><th>Refs</th><th>Self-assessment</th><th>Owner</th><th>Priority</th></> : <><th>Control</th><th>Date</th><th>Samples</th><th>Deviations</th><th>Result</th></>}</tr></thead>
            <tbody>{log.preview.slice(0, 25).map((p: any, i: number) => <tr key={i}>{kind === "assessment" ? <><td className="small"><span className="mono">{p.control}</span> {p.title}</td><td className="mono faint">{p.refs.join(", ")}</td><td><span className="pill">{p.self_assessment}</span></td><td className="small">{p.owner}</td><td>{p.priority || "—"}</td></>
              : <><td className="small"><span className="mono">{p.control}</span></td><td className="small">{p.test_date}</td><td>{p.samples}</td><td>{p.deviations}</td><td>{p.result || "computed"}</td></>}</tr>)}</tbody></table>}
        </>}
      </div>
    </div>
  );
}
