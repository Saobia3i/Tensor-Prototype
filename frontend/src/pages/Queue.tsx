import { Err, Loading, Pill, dt, go, pct, useApi } from "../ui";

export default function Queue() {
  const q = useApi<any[]>("/v1/review-queue");
  return (
    <>
      <h1>Expert review queue</h1>
      <p className="sub">Every AI proposal waits here for a named reviewer. Lowest confidence first. Nothing is applied until you approve it.</p>
      <Err e={q.error} />
      {!q.data ? <Loading /> : q.data.length === 0 ? <div className="card muted">Queue is empty.</div> :
        <div className="card"><table>
          <thead><tr><th>Item</th><th>Client · engagement</th><th>Type</th><th>AI decision</th><th>Confidence</th><th>Flags</th><th>Created</th></tr></thead>
          <tbody>{q.data.map((a) => (
            <tr key={a.id} className="click" onClick={() => go(a.subject_type === "evidence" ? `/evidence/${a.subject_id}` : `/engagements/${a.engagement_id}/questionnaires`)}>
              <td><b>{a.subject_title || a.subject_id.slice(0, 8)}</b></td>
              <td>{a.client}<br /><span className="muted small">{a.engagement}</span></td>
              <td><span className="pill">{a.task_type.replace(/_/g, " ")}</span></td>
              <td><Pill s={a.decision} /></td><td>{pct(a.confidence)}</td>
              <td>{a.warnings?.length ? <span className="pill warn">{a.warnings.length} warning{a.warnings.length > 1 ? "s" : ""}</span> : ""}</td>
              <td className="small muted">{dt(a.created_at)}</td>
            </tr>))}</tbody>
        </table></div>}
    </>
  );
}
