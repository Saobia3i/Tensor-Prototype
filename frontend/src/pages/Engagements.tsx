import { useState } from "react";
import { api } from "../api";
import { Bar, Err, Loading, Pill, d, go, money, useAction, useApi, useMe } from "../ui";

export default function Engagements() {
  const me = useMe();
  const engs = useApi<any[]>("/v1/engagements");
  const tenants = useApi<any[]>("/v1/tenants");
  const [show, setShow] = useState(false);
  const names = Object.fromEntries((tenants.data || []).map((t) => [t.id, t.name]));
  if (!me.is_staff && engs.data?.length === 1) { go(`/engagements/${engs.data[0].id}`); return null; }
  return (
    <>
      <div className="spread">
        <div><h1>{me.is_staff ? "Engagements" : "Your compliance program"}</h1>
          <p className="sub">{me.is_staff ? "Every client engagement you are assigned to." : "Delivered by TensorInfoSec experts."}</p></div>
        {me.is_staff && <button className="btn primary" onClick={() => setShow(!show)}>New engagement</button>}
      </div>
      {show && <NewEngagement tenants={(tenants.data || []).filter((t) => t.my_role === "delivery_lead")} onDone={() => { setShow(false); engs.reload(); tenants.reload(); }} />}
      <Err e={engs.error} />
      {!engs.data ? <Loading /> : <div className="card"><table>
        <thead><tr><th>Client</th><th>Engagement</th><th>Frameworks</th><th>Status</th><th style={{ width: 160 }}>Controls evidenced</th><th>Target</th>{me.is_staff && <th>Value</th>}</tr></thead>
        <tbody>{engs.data.map((e) => (
          <tr key={e.id} className="click" onClick={() => go(`/engagements/${e.id}`)}>
            <td>{names[e.tenant_id] || "—"}</td><td><b>{e.name}</b></td><td>{e.frameworks.join(", ")}</td><td><Pill s={e.status} /></td>
            <td><Bar v={e.controls_total ? (100 * e.controls_evidenced) / e.controls_total : 0} /><span className="small muted">{e.controls_evidenced} / {e.controls_total}</span></td>
            <td>{d(e.target_date)}</td>{me.is_staff && <td>{money(e.contract_value_usd)}</td>}
          </tr>))}</tbody></table></div>}
    </>
  );
}

function NewEngagement({ tenants, onDone }: { tenants: any[]; onDone: () => void }) {
  const staff = useApi<any[]>("/v1/staff");
  const { busy, run } = useAction();
  const [f, setF] = useState({ tenant_id: "", new_client: "", employees: "30", name: "SOC 2 Audit-Ready Year", fw: ["SOC2"], signer_id: "", value: "7500" });
  const set = (k: string, v: any) => setF({ ...f, [k]: v });
  const submit = () => run(async () => {
    let tid = f.tenant_id;
    if (!tid) tid = (await api.post("/v1/tenants", { name: f.new_client, employee_count: Number(f.employees) || null })).id;
    await api.post("/v1/engagements", { tenant_id: tid, name: f.name, frameworks: f.fw, signer_id: f.signer_id || null, contract_value_usd: Number(f.value) || 0 });
  }, "Engagement created with controls and evidence requests", onDone);
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <h2>New engagement</h2>
      <div className="grid g3">
        <label className="f"><span>Client</span><select value={f.tenant_id} onChange={(e) => set("tenant_id", e.target.value)}>
          <option value="">+ New client…</option>{tenants.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
        {!f.tenant_id && <label className="f"><span>New client name</span><input value={f.new_client} onChange={(e) => set("new_client", e.target.value)} /></label>}
        {!f.tenant_id && <label className="f"><span>Employees</span><input value={f.employees} onChange={(e) => set("employees", e.target.value)} /></label>}
        <label className="f"><span>Name</span><input value={f.name} onChange={(e) => set("name", e.target.value)} /></label>
        <label className="f"><span>Named signer</span><select value={f.signer_id} onChange={(e) => set("signer_id", e.target.value)}>
          <option value="">Choose…</option>{(staff.data || []).filter((s) => s.can_sign).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
        <label className="f"><span>Contract value (USD / yr)</span><input value={f.value} onChange={(e) => set("value", e.target.value)} /></label>
      </div>
      <div className="row" style={{ marginBottom: 12 }}>{["SOC2", "ISO27001", "ISO42001", "PCIDSS"].map((x) =>
        <label key={x} className="row small" style={{ gap: 4 }}><input type="checkbox" style={{ width: "auto" }} checked={f.fw.includes(x)}
          onChange={(e) => set("fw", e.target.checked ? [...f.fw, x] : f.fw.filter((y) => y !== x))} />{x}</label>)}</div>
      <button className="btn primary" disabled={busy || !f.fw.length || (!f.tenant_id && !f.new_client)} onClick={submit}>Create</button>
    </div>
  );
}
