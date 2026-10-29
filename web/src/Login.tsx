import { useEffect, useState } from "react";
import { api, setSession, type AuthUser } from "./api";

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
      setErr(String(e).replace("Error: ", ""));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <div className="login-brand">
        <div className="mark">
          <span className="dot" />
          <span>OI 错题诊断</span>
        </div>
        <div>
          <h1>
            看懂一次报错，
            <br />
            自己完成下一次修改。
          </h1>
          <p>
            查看编译与运行记录，按提示检查代码。
            <br />
            修改后重新提交，用新的执行结果验证你的判断。
          </p>
        </div>
        <div className="foot">本地教学工作台 · C++17</div>
      </div>

      <div className="login-panel">
        <form className="login-card" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
          <h2>登录</h2>
          <p className="hint">登录后可继续本机草稿。{demos.length > 0 ? "也可选择下方演示账号。" : "请使用教师分配的账号。"}</p>

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

          <button type="submit" className="btn-primary" disabled={busy || !username.trim() || !password} style={{ marginTop: 8 }}>
            {busy ? "登录中…" : "进入系统"}
          </button>

          {demos.length > 0 && <div className="demo-accounts">
            <div className="label">一键填入演示账号</div>
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
                    {ROLE_LABEL[d.role]} · {d.username}
                  </span>
                </button>
              ))}
            </div>
          </div>}
        </form>
      </div>
    </div>
  );
}
