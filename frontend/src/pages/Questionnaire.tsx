import { useState } from "react";
import { api, openAuthed } from "../api";
import { Err, Loading, Pill, pct, useAction, useApi, useMe, can } from "../ui";

export default function QuestionnairePage({ id }: { id: string }) {
  const me = useMe();
  const q = useApi(`/v1/questionnaires/${id}`);
  const { busy, run } = useAction();
  const [edit, setEdit] = useState<Record<string, string>>({});
  if (q.error) return <Err e={q.error} />;
  if (!q.data) return <Loading />;
  const x = q.data;
  const tenant = me.memberships.find((m) => can(me, m.tenant_id, "questionnaire.manage"))?.tenant_id;
  const staff = !!tenant && me.is_staff;
  const approved = x.items.filter((i: any) => i.status === "approved").length;
  return (
    <>
      <p className="small" style={{ margin: 0 }}><a href={`#/engagements/${x.engagement_id}/questionnaires`}>← Questionnaires</a></p>
      <div className="spread"><div><h1>{x.name}</h1><div className="row small"><Pill s={x.status} /><span className="muted">{approved} / {x.items.length} answers approved</span>
        {x.signoff && <span className="muted">signed by {x.signoff.signer}</span>}</div></div>
        <div className="row">
          {staff && <button className="btn sm" disabled={busy} onClick={() => run(() => api.post(`/v1/questionnaires/${id}/draft`), "Drafting queued", () => setTimeout(q.reload, 1500))}>Draft remaining with AI</button>}
          {staff && me.can_sign && x.status !== "approved" && <button className="btn ok sm" disabled={busy || approved < x.items.length} onClick={() => run(() => api.post(`/v1/questionnaires/${id}/approve`), "Signed off", q.reload)}>Sign off questionnaire</button>}
          {x.status === "approved" && <button className="btn primary sm" onClick={() => run(() => openAuthed(`/v1/questionnaires/${id}/export`))}>Export CSV</button>}
        </div></div>
      <div className="card" style={{ marginTop: 14 }}><table>
        <thead><tr><th style={{ width: 30 }}>#</th><th>Question</th><th>Answer</th><th>Status</th></tr></thead>
        <tbody>{x.items.map((i: any) => {
          const out = i.artifact?.output;
          return <tr key={i.id}>
            <td className="muted">{i.ordinal}</td>
            <td style={{ maxWidth: 320 }}>{i.question}</td>
            <td>
              {staff && i.status !== "approved" ? <>
                <textarea style={{ minHeight: 60 }} value={edit[i.id] ?? i.answer ?? ""} placeholder={out?.follow_up_question || "Write an answer…"} onChange={(e) => setEdit({ ...edit, [i.id]: e.target.value })} />
                {out && <p className="small muted" style={{ margin: "4px 0" }}>AI: <Pill s={out.answer_type} /> {pct(out.confidence)} · cites {out.citations.map((c: any) => `“${c.quote.slice(0, 60)}…”`).join("; ") || "nothing"}</p>}
                {i.artifact?.warnings?.map((w: string, k: number) => <div key={k} className="warnbox">{w}</div>)}
                <div className="row">
                  {i.artifact?.review_state === "proposed" && edit[i.id] === undefined && out?.answer_type !== "insufficient_evidence" &&
                    <button className="btn ok sm" disabled={busy} onClick={() => run(() => api.post(`/v1/ai-artifacts/${i.artifact.id}/review`, { decision: "approve", expected_version: i.artifact.version }), "Approved", q.reload)}>Approve AI answer</button>}
                  {(edit[i.id] ?? "").trim() && <button className="btn sm" disabled={busy} onClick={() => run(() => api.post(`/v1/questionnaire-items/${i.id}/answer`, { answer: edit[i.id] }), "Saved and approved", () => { setEdit({ ...edit, [i.id]: undefined as any }); q.reload(); })}>Save my answer</button>}
                </div>
              </> : <span>{i.answer || <span className="muted">—</span>}</span>}
            </td>
            <td><Pill s={i.status} /></td>
          </tr>;
        })}</tbody></table></div>
    </>
  );
}
