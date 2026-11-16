import { useEffect, useState } from "react";
import { api, setToken } from "../api";
import { Err } from "../ui";

type U = { id: string; email: string; name: string; is_staff: boolean };

export default function Login({ onIn }: { onIn: () => void }) {
  const [users, setUsers] = useState<U[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.get<U[]>("/v1/dev/users").then(setUsers).catch(() =>
      setErr("Development sign-in is disabled. In staging and production, sign in through your company's identity provider (OIDC with MFA)."));
  }, []);
  const pick = async (u: U) => {
    try { const r = await api.post("/v1/dev/login", { user_id: u.id }); setToken(r.access_token); onIn(); }
    catch (e: any) { setErr(e.message); }
  };
  return (
    <div className="login">
      <h1>Tensor Operations Console</h1>
      <p className="sub">AI drafts. Named experts decide. Local development sign-in with synthetic users only.</p>
      <Err e={err} />
      {users && <div className="grid g2">
        <div className="card"><h3>Tensor staff</h3>{users.filter((u) => u.is_staff).map((u) =>
          <button key={u.id} className="btn" onClick={() => pick(u)}><b>{u.name}</b><br /><span className="muted small">{u.email}</span></button>)}</div>
        <div className="card"><h3>Clients, partners, auditors</h3>{users.filter((u) => !u.is_staff).map((u) =>
          <button key={u.id} className="btn" onClick={() => pick(u)}><b>{u.name}</b><br /><span className="muted small">{u.email}</span></button>)}</div>
      </div>}
    </div>
  );
}
