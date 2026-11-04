import { useRef, useState } from "react";
import { api, messageOf, setSession, type AuthUser } from "../api";
import { ErrorNotice } from "./RecordUI";

export default function PasswordSettings({ required, onChanged, onCancel }: { required: boolean; onChanged: (user: AuthUser) => void; onCancel: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const lock = useRef(false);
  return <section className="password-settings">
    <h1>{required ? "请先修改临时密码" : "修改密码"}</h1>
    <p className="muted">{required ? "管理员已设置临时密码，修改后即可进入工作台。" : "修改成功后，其他页面上的旧登录会失效。"}新密码至少 8 个字符。</p>
    <ErrorNotice message={error} />
    <form className="admin-form" onSubmit={async (e) => {
      e.preventDefault(); if (lock.current) return;
      if (next !== repeat) { setError("两次新密码不一致。"); return; }
      lock.current = true; setBusy(true); setError("");
      try { const value = await api.changePassword(current, next); setSession(value.access_token, value.user); onChanged(value.user); }
      catch (err) { setError(messageOf(err)); } finally { lock.current = false; setBusy(false); }
    }}>
      <label>当前密码<input type="password" autoComplete="current-password" required maxLength={1024} value={current} onChange={(e) => setCurrent(e.target.value)} disabled={busy} /></label>
      <label>新密码<input type="password" autoComplete="new-password" required minLength={8} maxLength={128} value={next} onChange={(e) => setNext(e.target.value)} disabled={busy} /></label>
      <label>重复新密码<input type="password" autoComplete="new-password" required minLength={8} maxLength={128} value={repeat} onChange={(e) => setRepeat(e.target.value)} disabled={busy} /></label>
      <div className="admin-actions"><button className="btn-primary" disabled={busy}>{busy ? "正在修改…" : "保存新密码"}</button><button className="ghost" type="button" disabled={busy} onClick={onCancel}>{required ? "退出登录" : "取消"}</button></div>
    </form>
  </section>;
}
