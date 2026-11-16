import { useEffect, useMemo, useRef, useState } from "react";
import { api, openAuthed } from "../api";
import { Err, Loading, Pill, d, dt, go, pct, useAction, useApi } from "../ui";

function highlight(text: string, quotes: string[]) {
  const norm = (s: string) => s.replace(/\s+/g, " ").trim();
  for (const q of quotes) {
    const nq = norm(q);
    if (nq.length < 3) continue;
    const i = text.indexOf(q);
    if (i >= 0) return <>{text.slice(0, i)}<mark>{q}</mark>{text.slice(i + q.length)}</>;
  }
  return text;
}

export default function EvidencePage({ id }: { id: string }) {
  const ev = useApi(`/v1/evidence/${id}`);
  const ctl = useApi<any[]>(ev.data ? `/v1/engagements/${ev.data.engagement_id}/controls` : null);
  const opened = useRef(Date.now());
  useEffect(() => { opened.current = Date.now(); }, [id]);
  if (ev.error) return <Err e={ev.error} />;
  if (!ev.data) return <Loading />;
  const e = ev.data;
  const art = e.artifacts?.find((a: any) => a.review_state === "proposed") || e.artifacts?.[0];
  const out = art ? (art.final_output || art.output) : null;
  const citedHandles = new Map<string, string[]>();
  (out?.citations || []).forEach((c: any) => citedHandles.set(c.chunk_id, [...(citedHandles.get(c.chunk_id) || []), c.quote]));
  return (
    <>
      <p className="small" style={{ margin: 0 }}><a href={`#/engagements/${e.engagement_id}/evidence`}>← Evidence</a></p>
      <div className="spread">
        <div><h1>{e.title} <span className="muted small">v{e.version}</span></h1>
          <div className="row small"><Pill s={e.status} /><span className="muted">{e.filename}</span><span className="muted">source: {e.source}</span>
            <span className="mono muted">sha256 {e.sha256?.slice(0, 16)}…</span><span className="muted">collected {dt(e.collected_at)}</span></div></div>
        <div className="row">
          {!["awaiting_upload", "quarantined", "scanning", "rejected_security", "deleted", "duplicate"].includes(e.status) &&
            <button className="btn sm" onClick={() => openAuthed(`/v1/evidence/${e.id}/download`, "POST")}>Download original</button>}
          {e.can_review && ["extracted", "proposed", "expert_review", "needs_information"].includes(e.status) &&
            <button className="btn sm" onClick={() => api.post(`/v1/evidence/${e.id}/ai-review`).then(() => setTimeout(ev.reload, 1500))}>Re-run AI review</button>}
        </div>
      </div>
      {e.error && <div className="warnbox">{e.error}</div>}
      {e.versions?.length > 1 && <p className="small muted">Versions: {e.versions.map((v: any) =>
        <a key={v.id} href={`#/evidence/${v.id}`} style={{ marginRight: 8 }}>v{v.version} ({v.status})</a>)}</p>}
      <div className="split" style={{ marginTop: 14 }}>
        <div className="card">
          <h3>Extracted text <span className="muted small">({e.chunks.length} passages · page/offset provenance)</span></h3>
          {e.chunks.length === 0 && <p className="muted">No extractable text. {e.detected_type === "png" || e.detected_type === "jpeg" ? "Images need manual review (OCR not configured)." : ""}</p>}
          {e.chunks.map((c: any) => (
            <div key={c.id} className={`chunk ${citedHandles.has(c.handle) ? "cited" : ""}`}>
              <div className="h">{c.handle} · page {c.page} · chars {c.char_start}–{c.char_end}</div>
              {highlight(c.text, citedHandles.get(c.handle) || [])}
            </div>))}
        </div>
        <div>
          {e.can_review ? (art ? <Proposal art={art} controls={ctl.data || []} opened={opened.current} reload={ev.reload} /> :
            <div className="card muted">No AI proposal yet{["quarantined", "scanning"].includes(e.status) ? " (scanning…)" : ""}.</div>) :
            <div className="card"><h3>Mapped controls</h3>{e.links.filter((l: any) => l.status === "approved").map((l: any) =>
              <p key={l.id}><span className="mono">{l.control}</span> {l.title}</p>)}{e.links.length === 0 && <p className="muted">Pending expert review.</p>}</div>}
          {e.can_review && <ManualLink ev={e} controls={ctl.data || []} reload={ev.reload} />}
          {e.artifacts?.some((a: any) => a.decisions?.length) && <div className="card" style={{ marginTop: 14 }}><h3>Review history</h3>
            {e.artifacts.flatMap((a: any) => a.decisions.map((x: any, i: number) => <p key={a.id + i} className="small"><Pill s={x.decision === "approve" ? "approved" : x.decision === "edit" ? "edited" : "rejected"} /> {x.reviewer} · {dt(x.at)}{x.comment && <> — <i>{x.comment}</i></>}</p>))}</div>}
        </div>
      </div>
    </>
  );
}

function Proposal({ art, controls, opened, reload }: { art: any; controls: any[]; opened: number; reload: () => void }) {
  const out = art.final_output || art.output;
  const [keep, setKeep] = useState<Record<string, boolean>>(() => Object.fromEntries(out.candidate_controls.map((c: any) => [c.control_code, true])));
  const [rationale, setRationale] = useState<Record<string, string>>(() => Object.fromEntries(out.candidate_controls.map((c: any) => [c.control_code, c.rationale])));
  const [comment, setComment] = useState("");
  const { busy, run } = useAction();
  const editable = art.review_state === "proposed";
  const titles = useMemo(() => Object.fromEntries(controls.map((c) => [c.code, c.title])), [controls]);
  const changed = out.candidate_controls.some((c: any) => !keep[c.control_code] || rationale[c.control_code] !== c.rationale);
  const submit = (decision: "approve" | "reject" | "edit") => {
    const body: any = { decision, comment, expected_version: art.version, review_seconds: Math.round((Date.now() - opened) / 1000) };
    if (decision === "edit") {
      body.edited_output = { ...art.output, candidate_controls: art.output.candidate_controls.filter((c: any) => keep[c.control_code]).map((c: any) => ({ ...c, rationale: rationale[c.control_code] })) };
    }
    run(() => api.post(`/v1/ai-artifacts/${art.id}/review`, body), decision === "reject" ? "Rejected" : "Approved: evidence linked", reload);
  };
  return (
    <div className="card">
      <div className="spread"><h3>AI proposal <Pill s={art.review_state} /></h3><span className="small muted">confidence {pct(art.confidence)}</span></div>
      <p className="small muted" style={{ marginTop: -4 }}>A draft for your judgement. It has no authority to change anything.</p>
      {art.warnings?.map((w: string, i: number) => <div key={i} className="warnbox">⚠ {w}</div>)}
      <p><b>{out.evidence_type}</b> · <Pill s={out.decision} /></p>
      <p className="small">{out.summary}</p>
      {(out.period_start || out.period_end) && <p className="small">Period covered: {d(out.period_start)} → {d(out.period_end)}</p>}
      <h3 style={{ marginTop: 12 }}>Proposed control mappings</h3>
      {out.candidate_controls.length === 0 && <p className="muted small">None: the document does not support an in-scope control.</p>}
      {out.candidate_controls.map((c: any) => (
        <div key={c.control_code} style={{ borderTop: "1px solid var(--soft)", padding: "8px 0" }}>
          <label className="row" style={{ gap: 8 }}><input type="checkbox" style={{ width: "auto" }} disabled={!editable} checked={!!keep[c.control_code]} onChange={(e) => setKeep({ ...keep, [c.control_code]: e.target.checked })} />
            <span className="mono">{c.control_code}</span><b className="small">{titles[c.control_code]}</b><span className="muted small">cites {c.citation_chunk_ids.join(", ")}</span></label>
          {editable ? <input className="small" style={{ marginTop: 4 }} value={rationale[c.control_code] || ""} onChange={(e) => setRationale({ ...rationale, [c.control_code]: e.target.value })} />
            : <p className="small muted" style={{ margin: "4px 0 0" }}>{c.rationale}</p>}
        </div>))}
      {out.gaps?.length > 0 && <><h3 style={{ marginTop: 12 }}>Gaps / questions</h3><ul className="small">{out.gaps.map((g: string, i: number) => <li key={i}>{g}</li>)}</ul></>}
      {out.contradictions?.length > 0 && <><h3>Contradictions</h3><ul className="small">{out.contradictions.map((g: string, i: number) => <li key={i}>{g}</li>)}</ul></>}
      {editable && <>
        <label className="f" style={{ marginTop: 10 }}><span>Comment (required to reject)</span><input value={comment} onChange={(e) => setComment(e.target.value)} /></label>
        <div className="row">
          <button className="btn ok" disabled={busy} onClick={() => submit(changed ? "edit" : "approve")}>{changed ? "Approve with edits" : "Approve mappings"}</button>
          <button className="btn danger" disabled={busy || comment.trim().length < 5} onClick={() => submit("reject")}>Reject</button>
        </div>
      </>}
      <p className="mono muted" style={{ marginTop: 12, fontSize: 11 }}>artifact {art.id.slice(0, 8)} · run {art.ai_run_id?.slice(0, 8)} · <a href="#" onClick={(e) => { e.preventDefault(); api.get(`/v1/ai-artifacts/${art.id}`).then((a) => alert(JSON.stringify(a.run, null, 2))); }}>model & prompt version</a></p>
    </div>
  );
}

function ManualLink({ ev, controls, reload }: { ev: any; controls: any[]; reload: () => void }) {
  const [ec, setEc] = useState("");
  const [why, setWhy] = useState("");
  const { busy, run } = useAction();
  if (!["extracted", "proposed", "expert_review", "needs_information", "approved"].includes(ev.status)) return null;
  return (
    <div className="card" style={{ marginTop: 14 }}>
      <h3>Map manually (expert)</h3>
      <select value={ec} onChange={(e) => setEc(e.target.value)}><option value="">Choose control…</option>
        {controls.map((c) => <option key={c.id} value={c.id}>{c.code} · {c.title}</option>)}</select>
      <input style={{ marginTop: 6 }} placeholder="Rationale (recorded for the auditor)" value={why} onChange={(e) => setWhy(e.target.value)} />
      <button className="btn sm" style={{ marginTop: 8 }} disabled={busy || !ec || why.length < 5} onClick={() => run(() => api.post(`/v1/evidence/${ev.id}/links`, { engagement_control_id: ec, rationale: why }), "Linked and approved", () => { setWhy(""); reload(); })}>Link & approve</button>
      <p className="small muted" style={{ marginBottom: 0 }}>Back to <a href="#" onClick={(e) => { e.preventDefault(); go("/queue"); }}>review queue</a></p>
    </div>
  );
}
