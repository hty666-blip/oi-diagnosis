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
  const [registering, setRegistering] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [demos, setDemos] = useState<Demo[]>([]);

  useEffect(() => {
    api.demoAccounts().then(setDemos).catch(() => setDemos([]));
  }, []);

  const ready = username.trim() && password && (!registering || (displayName.trim() && inviteCode.trim()));

  function changeMode(next: boolean) {
    setRegistering(next);
    setPassword("");
    setDisplayName("");
    setInviteCode("");
    setErr("");
  }

  async function submit() {
    if (busy || !ready) return;
    setBusy(true);
    setErr("");
    try {
      const r = registering ? await api.registerStudent({
        username: username.trim(), password, display_name: displayName.trim(), invite_code: inviteCode.trim(),
      }) : await api.login(username.trim(), password);
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
            <h2 id="login-title">{registering ? "学生注册" : "登录"}</h2>
            <p className="hint">{registering ? "使用班级提供的一次性邀请码注册。" : demos.length > 0 ? "输入账号，或选择演示账号。" : "使用分配的账号登录。"}</p>

            {registering && <>
              <div className="field">
                <label htmlFor="register-invite">班级邀请码</label>
                <input id="register-invite" type="password" autoComplete="off" value={inviteCode}
                  onChange={(event) => setInviteCode(event.target.value)} required maxLength={36} disabled={busy} />
              </div>
              <div className="field">
                <label htmlFor="register-name">姓名或昵称</label>
                <input id="register-name" autoComplete="nickname" value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)} required maxLength={64} disabled={busy} />
              </div>
            </>}

            <div className="field">
              <label htmlFor="login-username">用户名</label>
              <input id="login-username" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username"
                pattern={registering ? "[A-Za-z0-9][A-Za-z0-9_.\\-]*" : undefined}
                title={registering ? "以字母或数字开头，可使用字母、数字、点、下划线和短横线" : undefined}
                maxLength={64} required disabled={busy} />
            </div>
            <div className="field">
              <label htmlFor="login-password">密码</label>
              <input
                type="password"
                id="login-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={registering ? "new-password" : "current-password"}
                minLength={registering ? 8 : undefined}
                maxLength={registering ? 128 : 1024}
                required
                disabled={busy}
              />
            </div>

            {err && <div className="err" role="alert">{err}</div>}

            <button type="submit" className="btn-primary" disabled={busy || !ready}>
              {registering ? (busy ? "注册中…" : "注册并进入系统") : (busy ? "登录中…" : "进入系统")}
            </button>
            <button type="button" className="ghost login-mode" disabled={busy} onClick={() => changeMode(!registering)}>
              {registering ? "返回登录" : "使用邀请码注册"}
            </button>

            {!registering && demos.length > 0 && <div className="demo-accounts">
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
