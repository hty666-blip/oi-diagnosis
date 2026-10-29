import { useEffect, useRef, useState } from "react";
import Login from "./Login";
import Markdown from "./Markdown";
import { CardDownload, ErrorNotice, EvidencePanel, VerdictBadge } from "./components/RecordUI";
import ReviewPanel, { ReviewHistoryPanel } from "./components/ReviewPanel";
import SubmissionLineage from "./components/SubmissionLineage";
import {
  api, cachedUser, clearSession, messageOf, readLocal, writeLocal,
  type AuthUser, type CoachItem, type DiagResult, type EvidenceItem,
  type MySubmission, type Overview, type Problem, type ProblemDetail,
  type SessionReply, type SubmissionResult,
} from "./api";

type View = "submit" | "my" | "coach" | "admin";
const STARTER = "#include <iostream>\nusing namespace std;\n\nint main() {\n    // 在这里编写你的解题代码\n    return 0;\n}\n";
const ROLE_LABEL: Record<string, string> = { student: "学生", teacher: "教练", admin: "管理员" };

export default function App() {
  const [user, setUser] = useState<AuthUser | null>(cachedUser);
  const [view, setView] = useState<View>(() => cachedUser()?.role === "student" ? "submit" : "coach");
  const [restore, setRestore] = useState<{ id: number; tick: number } | null>(null);
  const [expired, setExpired] = useState(false);

  useEffect(() => {
    const onExpired = () => { setExpired(true); setUser(null); };
    window.addEventListener("oi-session-expired", onExpired);
    return () => window.removeEventListener("oi-session-expired", onExpired);
  }, []);

  if (!user) return <><Login onLogin={(next) => {
    setUser(next);
    setExpired(false);
    setRestore(null);
    setView(next.role === "student" ? "submit" : "coach");
  }} />{expired && <div className="session-notice" role="status">登录已过期，重新登录后可继续本机草稿。</div>}</>;

  const nav: { key: View; label: string }[] = user.role === "student"
    ? [{ key: "submit", label: "练习与诊断" }, { key: "my", label: "提交记录" }]
    : [{ key: "coach", label: "待复核记录" }, ...(user.role === "admin" ? [{ key: "admin" as View, label: "系统概览" }] : [])];

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><span className="dot" aria-hidden="true" /><span className="name">OI 诊断<small>看证据，自己改对</small></span></div>
        <nav className="nav-group" aria-label="主要导航">
          {nav.map((item) => <button key={item.key} className={"nav-item " + (view === item.key ? "active" : "")} aria-current={view === item.key ? "page" : undefined} onClick={() => setView(item.key)}>{item.label}</button>)}
        </nav>
        <div className="sidebar-foot"><div className="user-card">
          <div className="avatar" aria-hidden="true">{(user.display_name || user.username).slice(0, 1)}</div>
          <div className="who"><b>{user.display_name || user.username}</b><span>{ROLE_LABEL[user.role]}{user.class_name ? " · " + user.class_name : ""}</span></div>
          <button className="logout" onClick={() => { clearSession(); setUser(null); }}>退出</button>
        </div></div>
      </aside>
      <main className="main" id="main">
        {user.role === "student" && <div hidden={view !== "submit"}><SubmitView key={user.id} userId={user.id} restore={restore} /></div>}
        {view === "my" && <MyView onContinue={(id) => { setRestore({ id, tick: Date.now() }); setView("submit"); }} />}
        {view === "coach" && user.role !== "student" && <CoachView userId={user.id} />}
        {view === "admin" && user.role === "admin" && <AdminView />}
      </main>
    </div>
  );
}

function initialDiagnosis(sub: SubmissionResult): DiagResult | null {
  if (!sub.diagnosis) return null;
  return { ...sub.diagnosis, submission_id: sub.id, verdict: sub.verdict, route: sub.diagnosis.route ?? "" };
}

function SubmitView({ userId, restore }: { userId: number; restore: { id: number; tick: number } | null }) {
  const [problems, setProblems] = useState<Problem[]>([]);
  const [pid, setPid] = useState("");
  const [detail, setDetail] = useState<ProblemDetail | null>(null);
  const [code, setCode] = useState(STARTER);
  const [savedCode, setSavedCode] = useState("");
  const [loadingProblems, setLoadingProblems] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [problemError, setProblemError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [busy, setBusy] = useState<"judge" | "diagnose" | "restore" | null>(null);
  const [error, setError] = useState("");
  const [diagError, setDiagError] = useState("");
  const [sub, setSub] = useState<SubmissionResult | null>(null);
  const [diag, setDiag] = useState<DiagResult | null>(null);
  const [evidence, setEvidence] = useState<EvidenceItem[]>([]);
  const [evidenceError, setEvidenceError] = useState("");
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [tutorSubmission, setTutorSubmission] = useState<number | null>(null);
  const [latestSubmitted, setLatestSubmitted] = useState<SubmissionResult | null>(null);
  const [detailRetry, setDetailRetry] = useState(0);
  const [lineageRestore, setLineageRestore] = useState<{ id: number; tick: number } | null>(null);
  const restoreTarget = (lineageRestore?.tick ?? 0) > (restore?.tick ?? 0) ? lineageRestore : restore;
  const requestLock = useRef(false);
  const workRequest = useRef(0);
  const evidenceRequest = useRef(0);
  const hasSelection = useRef(false);
  const draftKey = (nextPid: string) => "oi-draft:" + userId + ":" + nextPid;

  function selectProblem(nextPid: string) {
    hasSelection.current = true;
    workRequest.current += 1;
    evidenceRequest.current += 1;
    setPid(nextPid);
    setCode(readLocal(draftKey(nextPid)) ?? STARTER);
    writeLocal("oi-selected-problem:" + userId, nextPid);
    setSub(null); setDiag(null); setEvidence([]); setError(""); setDiagError("");
    setEvidenceLoading(false); setEvidenceError("");
    setTutorSubmission(null); setLatestSubmitted(null);
  }

  async function loadProblems() {
    setLoadingProblems(true); setProblemError("");
    try {
      const rows = await api.problems();
      setProblems(rows);
      if (!hasSelection.current && rows.length) {
        const stored = readLocal("oi-selected-problem:" + userId);
        const first = rows.find((row) => row.pid === stored) ?? rows.find((row) => row.pid.startsWith("PTV-")) ?? rows[0];
        selectProblem(first.pid);
      }
    } catch (e) { setProblemError(messageOf(e)); } finally { setLoadingProblems(false); }
  }

  useEffect(() => { void loadProblems(); }, [userId]);

  useEffect(() => {
    if (!pid) return;
    let active = true;
    setLoadingDetail(true); setDetailError(""); setDetail(null);
    api.getProblem(pid).then((value) => { if (active) setDetail(value); })
      .catch((e) => { if (active) setDetailError(messageOf(e)); })
      .finally(() => { if (active) setLoadingDetail(false); });
    return () => { active = false; };
  }, [pid, detailRetry]);

  async function loadEvidence(id: number) {
    const currentRequest = ++evidenceRequest.current;
    setEvidenceLoading(true); setEvidenceError(""); setEvidence([]);
    try {
      const rows = await api.evidence(id);
      if (currentRequest === evidenceRequest.current) setEvidence(rows);
    } catch (e) {
      if (currentRequest === evidenceRequest.current) setEvidenceError(messageOf(e));
    } finally {
      if (currentRequest === evidenceRequest.current) setEvidenceLoading(false);
    }
  }

  useEffect(() => {
    if (!restoreTarget) return;
    let active = true;
    const generation = ++workRequest.current;
    evidenceRequest.current += 1;
    hasSelection.current = true;
    requestLock.current = true;
    setBusy("restore"); setError("");
    api.getSubmission(restoreTarget.id).then(async (value) => {
      if (!active || generation !== workRequest.current) return;
      if (!value.problem_pid || value.code === undefined) throw new Error("这条历史记录缺少题目或源码，请从题库重新打开题目。");
      setPid(value.problem_pid); setCode(value.code); setSavedCode(value.code);
      writeLocal(draftKey(value.problem_pid), value.code);
      writeLocal("oi-selected-problem:" + userId, value.problem_pid);
      setSub(value); setDiag(initialDiagnosis(value)); setDiagError("");
      setTutorSubmission(null); setLatestSubmitted(null);
      await loadEvidence(value.id);
    }).catch((e) => { if (active) setError(messageOf(e)); }).finally(() => {
      if (active && generation === workRequest.current) { setBusy(null); requestLock.current = false; }
    });
    return () => { active = false; };
  }, [restoreTarget]);

  async function diagnose(id: number, generation = workRequest.current) {
    setDiagError("");
    try {
      const result = await api.diagnose(id);
      if (generation === workRequest.current) setDiag(result);
    } catch (e) {
      if (generation === workRequest.current) setDiagError(messageOf(e));
    }
  }

  async function retryDiagnosis() {
    if (!sub || requestLock.current) return;
    const generation = workRequest.current;
    requestLock.current = true; setBusy("diagnose");
    try { await diagnose(sub.id, generation); } finally {
      if (generation === workRequest.current) { setBusy(null); requestLock.current = false; }
    }
  }

  async function run() {
    if (requestLock.current || !detail || !code.trim()) return;
    const generation = ++workRequest.current;
    evidenceRequest.current += 1;
    requestLock.current = true; setBusy("judge"); setError(""); setDiagError("");
    setSub(null); setDiag(null); setEvidence([]); setEvidenceError("");
    const submittedCode = code;
    const parentId = sub?.id;
    try {
      const value = await api.submit(pid, submittedCode, parentId);
      if (generation !== workRequest.current) return;
      setSub(value); setSavedCode(submittedCode); setDiag(initialDiagnosis(value)); setLatestSubmitted(value);
      const evidenceTask = loadEvidence(value.id);
      if (!["AC", "SAMPLE_PASS", "NO_TESTS"].includes(value.verdict ?? "")) {
        setBusy("diagnose");
        await diagnose(value.id, generation);
      }
      await evidenceTask;
    } catch (e) {
      if (generation === workRequest.current) {
        setSub(sub); setDiag(diag); setEvidence(evidence);
        setError(messageOf(e));
      }
    } finally {
      if (generation === workRequest.current) { setBusy(null); requestLock.current = false; }
    }
  }

  const hasChanged = sub !== null && code !== savedCode;
  const canTutor = sub && diag && !["AC", "SAMPLE_PASS", "NO_TESTS"].includes(sub.verdict ?? "");

  return (
    <>
      <div className="page-head"><div><h1>练习与诊断</h1><p>先看执行证据，再检查代码；修改后重新提交验证。</p></div>
        <label className="problem-picker"><span className="dim">选择题目</span><select aria-label="选择题目" value={pid} disabled={!!busy || loadingProblems} onChange={(e) => selectProblem(e.target.value)}>
          {!pid && <option value="">{loadingProblems ? "正在加载题目…" : "暂无可用题目"}</option>}
          {problems.map((problem) => <option key={problem.pid} value={problem.pid}>{problem.pid + " · " + problem.title}</option>)}
        </select></label>
      </div>
      <ErrorNotice message={problemError} onRetry={() => void loadProblems()} />
      {!loadingProblems && !problemError && !problems.length && <div className="card empty">题库还没有题目。请联系教师导入题面和测试数据后再练习。</div>}
      <ErrorNotice message={error} />
      {busy === "restore" && <p role="status">正在恢复提交记录…</p>}
      {pid && <div className="oj">
        <section className="card oj-statement" aria-label="题目内容">
          {loadingDetail && <div className="empty" role="status">正在加载题面…</div>}
          <ErrorNotice message={detailError} onRetry={() => setDetailRetry((value) => value + 1)} />
          {detail && <>
            <div className="oj-title"><h2>{detail.pid + " " + detail.title}</h2><div className="row">
              {detail.difficulty && <span className="badge">{detail.difficulty}</span>}
              {detail.tags.slice(0, 4).map((tag) => <span className="badge" key={tag}>{tag}</span>)}
            </div></div>
            <Markdown text={detail.statement_md} />
            <h3 className="sample-title">题目样例</h3>
            {!detail.samples.length && <p className="muted">题面未提供样例。提交后的测试范围以实际执行记录为准。</p>}
            {detail.samples.map((sample, index) => <div className="sample" key={index}><div><span className="dim">输入 {index + 1}</span><pre>{sample.input}</pre></div><div><span className="dim">预期输出 {index + 1}</span><pre>{sample.output}</pre></div></div>)}
            <p className="dim">来源：{detail.source || "未注明"}。通过样例不代表覆盖所有边界情况。</p>
          </>}
        </section>
        <section className="oj-work" aria-label="代码与执行结果">
          <div className="card">
            <div className="card-head"><label htmlFor="solution-code"><strong>你的代码 · C++17</strong></label><span className="sub">Ctrl / ⌘ + Enter 提交</span></div>
            <textarea id="solution-code" className="code" value={code} spellCheck={false} readOnly={busy === "restore"} onChange={(e) => { setCode(e.target.value); writeLocal(draftKey(pid), e.target.value); }} onKeyDown={(e) => {
              if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); void run(); }
            }} />
            <div className="row editor-actions"><button className="primary" onClick={() => void run()} disabled={!!busy || !detail || !code.trim()}>
              {busy === "judge" ? "正在编译与运行…" : busy === "diagnose" ? "正在分析证据…" : busy === "restore" ? "正在恢复…" : sub ? "重新提交验证" : "提交并验证"}
            </button><span className="dim">草稿保留在当前浏览器</span></div>
            {hasChanged && <p className="scope-note">代码已有修改。下方结果对应上次提交，请重新提交验证改动。</p>}
          </div>
          {sub && <section className="card" aria-label="本次执行结果" aria-live="polite">
            <div className="card-head"><h2>提交 #{sub.id}</h2><CardDownload id={sub.id} /></div>
            <div className="verdict-bar"><VerdictBadge verdict={sub.verdict} tests={sub.tests_passed} />
              {sub.tests_passed && sub.tests_passed.total > 0 && <span className="muted">{sub.tests_passed.scope === "package" ? "题包测试" : sub.tests_passed.scope === "samples" ? "样例测试" : "历史测试"} {sub.tests_passed.passed} / {sub.tests_passed.total} 通过</span>}
            </div>
            <ReviewHistoryPanel key={sub.id} submissionId={sub.id} pending={diag?.status === "needs_human"} />
            <p className="scope-note">{sub.verdict === "CE" ? "编译未通过，尚未运行测试。请先检查下方编译器信息。"
              : sub.verdict === "SAMPLE_PASS" ? "当前仅通过题面样例，尚未验证其他输入。请继续检查边界情况；这里不会把样例通过记为完整解决。"
              : sub.verdict === "NO_TESTS" ? "编译已完成，但没有可运行的测试数据，无法判断程序结果是否正确。"
              : sub.tests_passed?.scope === "package" ? "结果基于当前题包中的测试数据，不代表对所有可能输入的数学证明。"
              : sub.tests_passed?.scope === "samples" ? "本次仅使用题面样例测试，以下结论也仅限于这些执行记录。"
              : "这条记录尚未标注完整测试范围，重新提交后可查看实际验证范围。"}</p>
            {busy === "diagnose" && <p role="status" className="muted">执行结果已保留，正在分析已有证据…</p>}
            <ErrorNotice message={diagError ? "执行已完成，诊断未完成：" + diagError : ""} onRetry={() => void retryDiagnosis()} />
            {diag && !["SAMPLE_PASS", "NO_TESTS"].includes(sub.verdict ?? "") && <div className="diagnosis-block">
              <h3>{diag.reviewed_at ? "当前审定说明" : diag.route === "llm_hypothesis" ? "待核实的模型建议" : diag.status === "needs_human" ? "现有证据与下一步检查" : "自动诊断说明"}</h3>
              {diag.status === "needs_human" && <p className="muted">现有证据不足以确认根因，需要进一步检查或请教师复核。</p>}
              <p className="narrative">{diag.narrative}</p>
              {!!diag.nature.length && <ul className="findings">{diag.nature.map((finding, index) => <li key={index}>
                <span className="loc">{finding.line ? "第 " + finding.line + " 行" + (finding.col ? "，第 " + finding.col + " 列" : "") : "检查点"}</span>
                <span className="msg">{finding.message || finding.code}</span>
                {finding.hint && <span className="hint">建议：{finding.hint}</span>}
              </li>)}</ul>}
            </div>}
            <EvidencePanel items={evidence} loading={evidenceLoading} error={evidenceError} onRetry={() => void loadEvidence(sub.id)} />
            <SubmissionLineage key={sub.id} submissionId={sub.id} onOpen={(id) => setLineageRestore({ id, tick: Date.now() })} />
            {canTutor && !tutorSubmission && <button className="ghost" onClick={() => setTutorSubmission(sub.id)}>围绕这次提交获取提示</button>}
          </section>}
          {tutorSubmission && <TutorPanel key={tutorSubmission} submissionId={tutorSubmission} userId={userId} latestSubmitted={latestSubmitted} />}
        </section>
      </div>}
    </>
  );
}

type ChatMessage = { role: "tutor" | "student"; level: number; text: string };

function TutorPanel({ submissionId, userId, latestSubmitted }: { submissionId: number; userId: number; latestSubmitted: SubmissionResult | null }) {
  const [session, setSession] = useState<SessionReply | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [verifyRetry, setVerifyRetry] = useState(0);
  const [verifyError, setVerifyError] = useState("");
  const [verifying, setVerifying] = useState(false);
  const sendLock = useRef(false);
  const verified = useRef(new Set<number>());
  const chatRef = useRef<HTMLDivElement>(null);
  const sessionKey = "oi-hint-session:" + userId + ":" + submissionId;

  useEffect(() => {
    let active = true;
    setLoading(true); setError("");
    async function open() {
      const stored = Number(readLocal(sessionKey));
      if (stored > 0) {
        const history = await api.getSession(stored);
        if (!active) return;
        setSession({ ...history, reply: "", action: "hint" });
        setMessages(history.turns.map((turn) => ({ role: turn.role, level: turn.level, text: turn.content })));
      } else {
        const reply = await api.createSession(submissionId);
        if (!active) return;
        writeLocal(sessionKey, String(reply.session_id));
        setSession(reply);
        setMessages([{ role: "tutor", level: reply.level, text: reply.reply }]);
      }
    }
    void open().catch((e) => { if (active) setError(messageOf(e)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [submissionId, retry]);

  useEffect(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, [messages]);

  useEffect(() => {
    if (!session || !latestSubmitted || busy || latestSubmitted.id === submissionId || verified.current.has(latestSubmitted.id)) return;
    let active = true;
    setVerifying(true); setVerifyError("");
    api.verifySession(session.session_id, latestSubmitted.id).then((reply) => {
      if (!active) return;
      verified.current.add(latestSubmitted.id);
      setSession(reply);
      setMessages((rows) => [...rows, { role: "tutor", level: reply.level, text: reply.reply }]);
    }).catch((e) => { if (active) setVerifyError(messageOf(e)); }).finally(() => { if (active) setVerifying(false); });
    return () => { active = false; };
  }, [latestSubmitted?.id, session?.session_id, verifyRetry, busy]);

  async function send() {
    if (!session || !draft.trim() || sendLock.current || verifying || session.status !== "open") return;
    const content = draft.trim();
    sendLock.current = true; setBusy(true); setError("");
    try {
      const reply = await api.sendMessage(session.session_id, content);
      setMessages((rows) => [...rows, { role: "student", level: session.level, text: content }, { role: "tutor", level: reply.level, text: reply.reply }]);
      setSession(reply); setDraft("");
    } catch (e) { setError(messageOf(e)); } finally { setBusy(false); sendLock.current = false; }
  }

  return <section className="card" aria-label="提示与修改验证">
    <div className="card-head"><h2>提示与修改验证</h2><span className="sub">围绕提交 #{submissionId}</span></div>
    <p className="muted">先说说你的判断。修改上方代码后重新提交，系统会用新的执行结果检查是否解决。</p>
    {loading && <p role="status">正在打开提示记录…</p>}
    <ErrorNotice message={error} onRetry={!session ? () => setRetry((value) => value + 1) : undefined} />
    <ErrorNotice message={verifyError ? "新提交已完成，但会话验证失败：" + verifyError : ""} onRetry={() => setVerifyRetry((value) => value + 1)} />
    {verifying && <p role="status">正在用新提交核对修改结果…</p>}
    {session && <>
      <div className="chat" ref={chatRef} role="log" aria-label="提示记录" aria-live="polite">
        {messages.map((message, index) => <div key={index} className={"bubble " + message.role}><span className="lvl">{message.role === "tutor" ? "学习提示" : "我的思考"}</span>{message.text}</div>)}
      </div>
      {session.status === "open" ? <form className="composer" onSubmit={(e) => { e.preventDefault(); void send(); }}>
        <label className="sr-only" htmlFor={"hint-message-" + submissionId}>你的思路或问题</label>
        <textarea id={"hint-message-" + submissionId} className="text grow" rows={2} maxLength={4000} placeholder="我检查了……，还不理解的是……" value={draft} onChange={(e) => setDraft(e.target.value)} disabled={busy || verifying} />
        <button type="submit" className="primary" disabled={!draft.trim() || busy || verifying}>{busy ? "发送中…" : "发送思考"}</button>
      </form> : <p className="scope-note" role="status">{session.status === "resolved" ? "新提交已通过当前题包测试，本次问题已验证解决。" : "本次提示已暂停，请查看执行证据或联系教师继续检查。"}</p>}
    </>}
  </section>;
}

function MyView({ onContinue }: { onContinue: (id: number) => void }) {
  const [rows, setRows] = useState<MySubmission[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  async function load() {
    setLoading(true); setError("");
    try { setRows(await api.mySubmissions()); } catch (e) { setError(messageOf(e)); } finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, []);
  return <>
    <div className="page-head"><div><h1>提交记录</h1><p>重新打开代码和执行证据，继续修改。测试通过记录不等同于知识点掌握度。</p></div><button className="ghost" onClick={() => void load()} disabled={loading}>刷新记录</button></div>
    <ErrorNotice message={error} onRetry={() => void load()} />
    <div className="card">
      {loading ? <div className="empty" role="status">正在加载提交记录…</div> : !rows.length && !error ? <div className="empty">还没有提交记录。从「练习与诊断」选择一道题，提交你的第一份代码。</div> : <div className="table-scroll"><table><thead><tr><th>题目 / 提交</th><th>执行结果</th><th>诊断摘要</th><th>操作</th></tr></thead><tbody>
        {rows.map((row) => <tr key={row.id}><td><strong>{row.problem_pid || "题目记录"} {row.problem_title || ""}</strong><div className="dim">#{row.id}{row.parent_submission_id ? " · 修改自 #" + row.parent_submission_id : ""}{row.created_at ? " · " + new Date(row.created_at).toLocaleString("zh-CN") : ""}</div></td><td><VerdictBadge verdict={row.verdict} tests={row.tests_passed} /></td><td className="muted">{row.diagnosis_status === "needs_human" ? <span className="badge warn">{row.reviewer_id ? "教师已查看 · 仍待核对" : "待教师复核"}</span> : row.reviewer_id ? <span className="badge">教师已复核</span> : null}<p>{row.narrative || "尚无诊断，打开后可查看执行记录。"}</p></td><td><div className="row"><button className="ghost" onClick={() => onContinue(row.id)}>打开并继续</button><CardDownload id={row.id} /></div></td></tr>)}
      </tbody></table></div>}
    </div>
  </>;
}

function CoachView({ userId }: { userId: number }) {
  const [queue, setQueue] = useState<CoachItem[]>([]);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<number | null>(() => Number(readLocal("oi-selected-review:" + userId)) || null);
  async function load() {
    setLoading(true); setError("");
    try {
      const [items, summary] = await Promise.all([api.coachQueue(), api.coachOverview()]);
      setQueue(items); setOverview(summary);
    } catch (e) { setError(messageOf(e)); } finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, []);
  return <>
    <div className="page-head"><div><h1>待复核记录</h1><p>核对学生源码和执行证据，再确认、修订或退回诊断。每次处理都会留下审计记录。</p></div><button className="ghost" onClick={() => void load()} disabled={loading}>刷新队列</button></div>
    {overview && <p className="muted">当前可见提交 {overview.submissions} 条 · 诊断 {overview.diagnoses} 条 · 待复核 {overview.pending_review} 条</p>}
    <ErrorNotice message={error} onRetry={() => void load()} />
    <div className="card">{loading ? <div className="empty" role="status">正在加载待复核记录…</div> : !queue.length && !error ? <div className="empty">暂无待复核记录。已打开记录的审定历史仍保留在下方。</div> : <div className="table-scroll"><table><thead><tr><th>学生 / 题目</th><th>执行结果</th><th>待检查的解释</th><th>操作</th></tr></thead><tbody>
      {queue.map((row) => <tr key={row.diagnosis_id}><td><strong>{row.student_name || "学生提交"}</strong><div className="dim">{row.problem_pid} {row.problem_title} · #{row.submission_id}</div></td><td><VerdictBadge verdict={row.verdict} tests={row.tests_passed} /></td><td className="muted">{row.narrative || "暂无诊断摘要"}</td><td><button className="ghost" onClick={() => { setSelectedId(row.submission_id); writeLocal("oi-selected-review:" + userId, String(row.submission_id)); }}>{selectedId === row.submission_id ? "正在查看" : "查看并复核"}</button></td></tr>)}
    </tbody></table></div>}</div>
    {selectedId && <ReviewPanel key={selectedId} submissionId={selectedId} onReviewed={() => void load()} />}
  </>;
}

function AdminView() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api.coachOverview().then(setOverview).catch((e) => setError(messageOf(e))); }, []);
  return <>
    <div className="page-head"><div><h1>系统概览</h1><p>当前账号可见的提交与诊断记录。</p></div></div>
    <ErrorNotice message={error} />
    {!overview && !error && <p role="status">正在加载系统记录…</p>}
    {overview && <div className="card"><dl className="overview-list"><div><dt>提交记录</dt><dd>{overview.submissions}</dd></div><div><dt>诊断记录</dt><dd>{overview.diagnoses}</dd></div><div><dt>待人工复核</dt><dd>{overview.pending_review}</dd></div></dl><p className="muted">提交计数描述系统活动，不用于推断学生掌握度或真实教学效果。</p></div>}
  </>;
}
