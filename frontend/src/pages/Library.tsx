import { useState } from "react";
import { Loading, useApi } from "../ui";

export default function Library() {
  const [fw, setFw] = useState("");
  const c = useApi<any[]>(`/v1/controls${fw ? `?framework=${fw}` : ""}`);
  const f = useApi<any[]>("/v1/frameworks");
  return (
    <>
      <h1>Control library</h1>
      <p className="sub">Tensor-authored controls. Framework mappings cite requirement IDs only; no licensed standard text is stored. Mappings are drafts until an expert validates them.</p>
      <div className="row" style={{ marginBottom: 12 }}>
        <select style={{ width: 220 }} value={fw} onChange={(e) => setFw(e.target.value)}><option value="">All frameworks</option>
          {(f.data || []).map((x) => <option key={x.code} value={x.code}>{x.code} · {x.version}</option>)}</select>
        <span className="muted small">{c.data?.length ?? "…"} controls</span>
      </div>
      {(f.data || []).filter((x) => !fw || x.code === fw).map((x) => <p key={x.code} className="small muted" style={{ margin: "0 0 4px" }}><b>{x.name} {x.version}:</b> {x.license_note}</p>)}
      {!c.data ? <Loading /> : <div className="card" style={{ marginTop: 10 }}><table>
        <thead><tr><th>Control</th><th>Domain</th><th>Mappings</th><th>Est. hours</th></tr></thead>
        <tbody>{c.data.map((x) => <tr key={x.id}>
          <td><span className="mono">{x.code}</span> <b>{x.title}</b><br /><span className="small muted">{x.objective}</span></td>
          <td className="small">{x.domain}</td>
          <td className="small">{x.mappings.filter((m: any) => !fw || m.framework === fw).map((m: any) => <div key={m.framework + m.ref}><span className="mono">{m.framework} {m.ref}</span> <span className="muted">{m.topic}</span>{m.mapping_status === "draft" ? "" : " ✓"}</div>)}</td>
          <td>{x.est_hours}</td></tr>)}</tbody></table></div>}
    </>
  );
}
