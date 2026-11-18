import { useEffect, useState } from "react";
import { api, messageOf, setSession, type AuthUser } from "./api";

interface Demo {
  username: string;
  password: string;
  role: string;
  name: string;
}

const ROLE_LABEL: Record<string, string> = { student: "学生", teacher: "教练", admin: "管理员" };

export default function Login({ onLogin }: { onLogin: (u: AuthUser) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [demos, setDemos] = useState<Demo[]>([]);

  useEffect(() => {
    api.demoAccounts().then(setDemos).catch(() => setDemos([]));
  }, []);

  async function submit() {
    if (busy || !username.trim() || !password) return;
    setBusy(true);
    setErr("");
    try {
      const r = await api.login(username.trim(), password);
      setSession(r.access_token, r.user);
      onLogin(r.user);
    } catch (e) {
      setErr(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <header className="login-brand">
        <div className="mark">
          <span className="brand-mark" aria-hidden="true">OI</span>
          <span>错题诊断</span>
        </div>
      </header>

      <main className="login-workspace">
        <section className="login-intro" aria-labelledby="login-intro-title">
          <div className="login-intro-content">
            <h1 id="login-intro-title">
              <span>信息学竞赛</span>
              <span>错题诊断与答疑</span>
            </h1>
            <ol className="login-flow" role="list" aria-label="错题处理流程">
              <li><strong>作答与判分</strong><span>记录正误</span></li>
              <li><strong>诊断 Agent</strong><span>定位错因</span></li>
              <li><strong>答疑 Agent</strong><span>讲解与追问</span></li>
            </ol>
          </div>
        </section>

        <div className="login-panel">
          <form className="login-card" aria-labelledby="login-title" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
            <h2 id="login-title">登录</h2>
            <p className="hint">{demos.length > 0 ? "输入账号，或选择演示账号。" : "使用分配的账号登录。"}</p>

            <div className="field">
              <label htmlFor="login-username">用户名</label>
              <input id="login-username" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" maxLength={64} required disabled={busy} />
            </div>
            <div className="field">
              <label htmlFor="login-password">密码</label>
              <input
                type="password"
                id="login-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                maxLength={1024}
                required
                disabled={busy}
              />
            </div>

            {err && <div className="err" role="alert">{err}</div>}

            <button type="submit" className="btn-primary" disabled={busy || !username.trim() || !password}>
              {busy ? "登录中…" : "进入系统"}
            </button>

            {demos.length > 0 && <div className="demo-accounts">
              <div className="label">演示账号</div>
              <div className="demo-grid">
                {demos.map((d) => (
                  <button
                    key={d.username}
                    type="button"
                    className="demo-chip"
                    disabled={busy}
                    onClick={() => {
                      setUsername(d.username);
                      setPassword(d.password);
                    }}
                  >
                    <b>{d.name}</b>
                    <span>
                      {ROLE_LABEL[d.role] && ROLE_LABEL[d.role] !== d.name && `${ROLE_LABEL[d.role]} · `}{d.username}
                    </span>
                  </button>
                ))}
              </div>
            </div>}
          </form>
        </div>
      </main>
    </div>
  );
}
