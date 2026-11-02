import { sameSource, verificationRecovery, verifyWithRefresh } from "./tutor-verification";
import { indentedNewline, shouldSendMessage } from "./editor-input";
import { emptyQuestionDraft, questionDraftStore } from "./question-draft";
import { useEffect, useRef, useState } from "react";
import Login from "./Login";
import TrainingWorkspace, { type TrainingPractice } from "./components/TrainingWorkspace";
import StudentProfiles from "./components/StudentProfiles";
import AdminWorkspace from "./components/AdminWorkspace";
import PasswordSettings from "./components/PasswordSettings";
import Markdown from "./Markdown";
import { CardDownload, ErrorNotice, EvidencePanel, VerdictBadge } from "./components/RecordUI";
import ReviewPanel, { ReviewHistoryPanel } from "./components/ReviewPanel";
import SubmissionLineage from "./components/SubmissionLineage";
import { cancelAndRefresh, parsePendingId, pendingKey, pollSubmission, submissionPhase, taskLabel } from "./judge-task";
import JudgingQueuePanel from "./components/JudgingQueuePanel";
import {
  api, ApiError, cachedUser, checkSessionStorage, clearSession, messageOf, readLocal, removeLocal, writeLocal,
  type AuthUser, type CoachItem, type DiagResult, type EvidenceItem,
  type MySubmission, type Overview, type Problem, type ProblemDetail,
  type SessionReply, type SubmissionLanguage, type SubmissionResult,
} from "./api";

type View = "submit" | "my" | "coach" | "admin" | "trainings" | "profiles" | "password";
const LANGUAGES: { value: SubmissionLanguage; label: string }[] = [
  { value: "cpp17", label: "C++17" },
  { value: "c11", label: "C11" },
  { value: "java17", label: "Java 17" },
  { value: "python3", label: "Python 3" },
];
const LANGUAGE_LABEL = Object.fromEntries(LANGUAGES.map((item) => [item.value, item.label]));
const STARTERS: Record<SubmissionLanguage, string> = {
  cpp17: "#include <iostream>\nusing namespace std;\n\nint main() {\n    // 在这里编写你的解题代码\n    return 0;\n}\n",
  c11: "#include <stdio.h>\n\nint main(void) {\n    /* 在这里编写你的解题代码 */\n    return 0;\n}\n",
  java17: "import java.io.*;\nimport java.util.*;\n\npublic class Main {\n    public static void main(String[] args) throws Exception {\n        // 在这里编写你的解题代码\n    }\n}\n",
  python3: "# 在这里编写你的解题代码\n",
};
const DEFAULT_LANGUAGE: SubmissionLanguage = "cpp17";
const ROLE_LABEL: Record<string, string> = { student: "学生", teacher: "教练", admin: "管理员" };

function isLanguage(value: string | null | undefined): value is SubmissionLanguage {
  return LANGUAGES.some((item) => item.value === value);
}

const hintSessionKey = (userId: number, submissionId: number) => `oi-hint-session:${userId}:${submissionId}`;
const activeHintKey = (userId: number | string, pid: string) => `oi-active-hint:${userId}:${pid}`;
const lastSubmissionKey = (userId: number | string, pid: string) => `oi-last-submission:${userId}:${pid}`;

export default function App() {
  const [user, setUser] = useState<AuthUser | null>(cachedUser);
  const [checkingSession, setCheckingSession] = useState(() => cachedUser() !== null);
  const [view, setView] = useState<View>("trainings");
  const [restore, setRestore] = useState<{ id: number; tick: number } | null>(null);
  const [practice, setPractice] = useState<TrainingPractice | null>(null);
  const [trainingTarget, setTrainingTarget] = useState<number>();
  const [sessionNotice, setSessionNotice] = useState("");

  useEffect(() => {
    const onExpired = () => { setSessionNotice("登录已过期，重新登录后可继续本机草稿。"); setUser(null); };
    const onChanged = () => { setSessionNotice("登录账号已在其他页面变化，请重新登录；本机草稿已保留。"); setUser(null); setRestore(null); };
    const onStorage = (event: StorageEvent) => {
      if (event.key === null || event.key === "oi_token" || event.key === "oi_user") checkSessionStorage();
    };
    window.addEventListener("oi-session-expired", onExpired);
    window.addEventListener("oi-session-changed", onChanged);
    window.addEventListener("storage", onStorage);
    checkSessionStorage();
    return () => {
      window.removeEventListener("oi-session-expired", onExpired);
      window.removeEventListener("oi-session-changed", onChanged);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  useEffect(() => {
    if (!cachedUser()) { setCheckingSession(false); return; }
    let active = true;
    api.me().then((current) => {
      if (!active) return;
      // 请求已核对会话快照；验证身份不重新写共享存储，避免旧标签页覆盖新登录。
      setUser(current);
      setView(current.role === "admin" ? "admin" : "trainings");
    }).catch((error: unknown) => {
      if (!active || (error instanceof Error && error.name === "AbortError")) return;
      clearSession(); setUser(null); setSessionNotice(messageOf(error));
    }).finally(() => { if (active) setCheckingSession(false); });
    return () => { active = false; };
  }, []);

  if (checkingSession) return <div className="session-notice" role="status">正在验证登录状态…</div>;

  if (!user) return <><Login onLogin={(next) => {
    setUser(next);
    setSessionNotice("");
    setRestore(null);
    setPractice(null); setTrainingTarget(undefined); setView(next.role === "admin" ? "admin" : "trainings");
  }} />{sessionNotice && <div className="session-notice" role="status">{sessionNotice}</div>}</>;

  if (user.must_change_password || view === "password") return <PasswordSettings required={!!user.must_change_password} onChanged={(next) => { setUser(next); setView(next.role === "admin" ? "admin" : "trainings"); }} onCancel={() => { if (user.must_change_password) { clearSession(); setUser(null); } else setView(user.role === "admin" ? "admin" : "trainings"); }} />;

  const nav: { key: View; label: string }[] = user.role === "student"
    ? [{ key: "trainings", label: "我的训练" }, { key: "submit", label: "自由练习" }, { key: "my", label: "提交记录" }, { key: "profiles", label: "我的档案" }]
    : [...(user.role === "admin" ? [{ key: "admin" as View, label: "平台管理" }] : []), { key: "trainings", label: "训练工作台" }, { key: "profiles", label: "学生档案" }, { key: "coach", label: "诊断复核" }];
  const openPractice = (value: TrainingPractice) => { setPractice(value); setRestore(value.submissionId ? { id: value.submissionId, tick: Date.now() } : null); setView("submit"); };

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><span className="dot" aria-hidden="true" /><span className="name">OI 诊断<small>看证据，自己改对</small></span></div>
        <nav className="nav-group" aria-label="主要导航">
          {nav.map((item) => <button key={item.key} className={"nav-item " + (view === item.key ? "active" : "")} aria-current={view === item.key ? "page" : undefined} onClick={() => { if (item.key === "submit") { setPractice(null); setRestore(null); } if (item.key === "trainings") setTrainingTarget(undefined); setView(item.key); }}>{item.label}</button>)}
        </nav>
        <button className="ghost" onClick={() => setView("password")}>修改密码</button>
        <div className="sidebar-foot"><div className="user-card">
          <div className="avatar" aria-hidden="true">{(user.display_name || user.username).slice(0, 1)}</div>
          <div className="who"><b>{user.display_name || user.username}</b><span>{ROLE_LABEL[user.role]}{user.class_name ? " · " + user.class_name : ""}</span></div>
          <button className="logout" onClick={() => { clearSession(); setUser(null); }}>退出</button>
        </div></div>
      </aside>
      <main className="main" id="main">
        {user.role === "student" && <div hidden={view !== "submit"}><SubmitView key={`${user.id}:${practice?.id ?? "free"}:${practice?.pid ?? ""}`} userId={user.id} restore={restore} training={practice ?? undefined} /></div>}
        {view === "trainings" && <TrainingWorkspace user={user} initialTrainingId={trainingTarget} onPractice={openPractice} />}
        {view === "profiles" && <StudentProfiles user={user} onPractice={openPractice} onTraining={(id) => { setTrainingTarget(id); setView("trainings"); }} />}
        {view === "my" && <MyView onContinue={(id) => { setPractice(null); setRestore({ id, tick: Date.now() }); setView("submit"); }} />}
        {view === "coach" && user.role !== "student" && <CoachView userId={user.id} />}
        {view === "admin" && user.role === "admin" && <AdminWorkspace onTeaching={() => setView("trainings")} onReview={() => setView("coach")} />}
      </main>
    </div>
  );
}

function initialDiagnosis(sub: SubmissionResult): DiagResult | null {
  if (!sub.diagnosis) return null;
  return { ...sub.diagnosis, submission_id: sub.id, verdict: sub.verdict, route: sub.diagnosis.route ?? "" };
}

function SubmitView({ userId, restore, training }: { userId: number; restore: { id: number; tick: number } | null; training?: TrainingPractice }) {
  const storageId = training ? `${userId}:training:${training.id}` : userId;
  const [problems, setProblems] = useState<Problem[]>([]);
  const [pid, setPid] = useState("");
  const [detail, setDetail] = useState<ProblemDetail | null>(null);
  const [language, setLanguage] = useState<SubmissionLanguage>(DEFAULT_LANGUAGE);
  const [code, setCode] = useState(STARTERS[DEFAULT_LANGUAGE]);
  const [savedCode, setSavedCode] = useState("");
  const [savedLanguage, setSavedLanguage] = useState<SubmissionLanguage>(DEFAULT_LANGUAGE);
  const [loadingProblems, setLoadingProblems] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [problemError, setProblemError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [busy, setBusy] = useState<"judge" | "diagnose" | "restore" | "cancel" | null>(null);
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
  const [pendingId, setPendingId] = useState<number | null>(null);
  const [polling, setPolling] = useState(false);
  const [taskError, setTaskError] = useState("");
  const restoreTarget = (lineageRestore?.tick ?? 0) > (restore?.tick ?? 0) ? lineageRestore : restore;
  const requestLock = useRef(false);
  const workRequest = useRef(0);
  const evidenceRequest = useRef(0);
  const hasSelection = useRef(false);
  const taskController = useRef<AbortController | null>(null);
  const submitController = useRef<AbortController | null>(null);
  const editorRef = useRef<HTMLTextAreaElement>(null);
  const draftKey = (nextPid: string, nextLanguage: SubmissionLanguage) =>
    `oi-draft:${storageId}:${nextPid}:${nextLanguage}`;
  const languageKey = (nextPid: string) => `oi-language:${storageId}:${nextPid}`;
  const pendingHintKey = (nextPid: string) => "oi-pending-hint:" + storageId + ":" + nextPid;

  function savedDraft(nextPid: string, nextLanguage: SubmissionLanguage): string {
    const current = readLocal(draftKey(nextPid, nextLanguage));
    if (current !== null) return current;
    // 兼容升级前仅按题目保存的 C++ 草稿。
    if (nextLanguage === "cpp17") {
      const legacy = readLocal(`oi-draft:${storageId}:${nextPid}`);
      if (legacy !== null) return legacy;
    }
    return STARTERS[nextLanguage];
  }

  useEffect(() => () => {
    workRequest.current += 1;
    taskController.current?.abort();
    submitController.current?.abort();
  }, []);

  function stopWatching() {
    taskController.current?.abort();
    taskController.current = null;
    setPolling(false); setPendingId(null); setTaskError("");
  }

  async function finishSubmission(value: SubmissionResult, nextPid: string, generation: number) {
    if (generation !== workRequest.current) return;
    if (parsePendingId(readLocal(pendingKey(storageId, nextPid))) === value.id) {
      removeLocal(pendingKey(storageId, nextPid));
      removeLocal(pendingHintKey(nextPid));
    }
    setPendingId(null); setTaskError(""); setSub(value); setDiag(initialDiagnosis(value));
    setSavedCode(value.code ?? ""); setSavedLanguage(isLanguage(value.language) ? value.language : DEFAULT_LANGUAGE);
    setLatestSubmitted(value);
    writeLocal(lastSubmissionKey(storageId, nextPid), String(value.id));
    const evidenceTask = loadEvidence(value.id);
    if (!["AC", "SAMPLE_PASS", "NO_TESTS"].includes(value.verdict ?? "") && !value.diagnosis?.reviewer_id) {
      requestLock.current = true; setBusy("diagnose");
      try { await diagnose(value.id, generation); }
      finally { if (generation === workRequest.current) { requestLock.current = false; setBusy(null); } }
    }
    await evidenceTask;
  }

  async function watchTask(id: number, nextPid: string, generation: number) {
    taskController.current?.abort();
    const controller = new AbortController();
    taskController.current = controller;
    setPendingId(id); setPolling(true); setTaskError("");
    writeLocal(pendingKey(storageId, nextPid), String(id));
    const result = await pollSubmission({
      id, signal: controller.signal,
      fetchRecord: async (recordId, signal) => {
        const value = await api.getSubmission(recordId, signal);
        if (value.problem_pid && value.problem_pid !== nextPid) throw new ApiError("保存的任务不属于当前题目，请从提交记录打开。", 404);
        return value;
      },
      isCurrent: () => generation === workRequest.current,
      onUpdate: (value) => {
        setSub(value); setSavedCode(value.code ?? "");
        setSavedLanguage(isLanguage(value.language) ? value.language : DEFAULT_LANGUAGE);
        if (submissionPhase(value) !== "complete") setDiag(null);
      },
    });
    if (generation !== workRequest.current || taskController.current !== controller) return;
    taskController.current = null; setPolling(false);
    if (result.reason === "complete" && result.record) await finishSubmission(result.record, nextPid, generation);
    else if (result.reason === "paused") {
      if (result.error instanceof ApiError && [403, 404].includes(result.error.status)) {
        removeLocal(pendingKey(storageId, nextPid)); removeLocal(pendingHintKey(nextPid));
        setPendingId(null); setSub(null);
        setError("保存的任务不存在或当前账号无权访问。本机草稿已保留，请从提交记录核对，或重新提交代码。");
      } else setTaskError(result.error ? messageOf(result.error) : "已暂停自动查询。任务仍保存在服务器，点击继续查询即可查看，无需重新提交。");
    } else if (result.reason === "failed") setTaskError("");
    else if (result.reason === "task_cancelled") {
      removeLocal(pendingHintKey(nextPid));
      setTaskError(""); setLatestSubmitted(null);
    }
  }

  function selectProblem(nextPid: string) {
    stopWatching();
    hasSelection.current = true;
    workRequest.current += 1;
    evidenceRequest.current += 1;
    setPid(nextPid);
    const storedLanguage = readLocal(languageKey(nextPid));
    const nextLanguage = isLanguage(storedLanguage) ? storedLanguage : DEFAULT_LANGUAGE;
    setLanguage(nextLanguage); setCode(savedDraft(nextPid, nextLanguage));
    writeLocal("oi-selected-problem:" + userId, nextPid);
    setSub(null); setDiag(null); setEvidence([]); setError(""); setDiagError("");
    setEvidenceLoading(false); setEvidenceError("");
    const storedId = parsePendingId(readLocal(pendingKey(storageId, nextPid)));
    setTutorSubmission(storedId ? parsePendingId(readLocal(pendingHintKey(nextPid))) : null);
    setLatestSubmitted(null);
    if (storedId) void watchTask(storedId, nextPid, workRequest.current);
    else {
      const lastId = parsePendingId(readLocal(lastSubmissionKey(storageId, nextPid)));
      const activeId = parsePendingId(readLocal(activeHintKey(storageId, nextPid)));
      if (lastId || activeId) void restoreSubmission(lastId ?? activeId!, nextPid);
    }
  }

  function selectLanguage(nextLanguage: SubmissionLanguage) {
    if (!pid || nextLanguage === language || busy || (sub && submissionPhase(sub) === "pending") || (!sub && pendingId)) return;
    setLanguage(nextLanguage);
    writeLocal(languageKey(pid), nextLanguage);
    setCode(savedDraft(pid, nextLanguage));
  }

  function startCurrentPractice() {
    if (training || busy || !pid || !sub || submissionPhase(sub) === "pending" || !problems.some((p) => p.pid === pid)) return;
    stopWatching(); workRequest.current++; evidenceRequest.current++;
    removeLocal(lastSubmissionKey(storageId, pid)); removeLocal(activeHintKey(storageId, pid));
    removeLocal(pendingKey(storageId, pid)); removeLocal(pendingHintKey(pid));
    setSub(null); setDiag(null); setEvidence([]); setTutorSubmission(null); setLatestSubmitted(null);
    setError(""); setDiagError(""); setEvidenceError(""); setEvidenceLoading(false);
    setSavedCode(""); setDetail(null); setDetailRetry((value) => value + 1);
  }

  async function loadProblems() {
    setLoadingProblems(true); setProblemError("");
    try {
      const rows = await api.problems();
      setProblems(rows);
      if (!hasSelection.current && (rows.length || training)) {
        if (training) { selectProblem(training.pid); return; }
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
    api.getProblem(pid, training?.id, sub?.problem_pid === pid && !training ? sub.id : undefined).then((value) => { if (active) setDetail(value); })
      .catch((e) => { if (active) setDetailError(messageOf(e)); })
      .finally(() => { if (active) setLoadingDetail(false); });
    return () => { active = false; };
  }, [pid, detailRetry, training?.id, sub?.id, sub?.problem_pid]);

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

  function restoreSubmission(id: number, expectedPid?: string) {
    stopWatching();
    const generation = ++workRequest.current;
    evidenceRequest.current += 1;
    hasSelection.current = true;
    requestLock.current = true;
    const controller = new AbortController();
    submitController.current?.abort(); submitController.current = controller;
    setBusy("restore"); setError("");
    api.getSubmission(id, controller.signal).then(async (value) => {
      if (generation !== workRequest.current) return;
      if (!value.problem_pid || value.code === undefined) throw new Error("这条历史记录缺少题目或源码，请从题库重新打开题目。");
      if (training && (value.training_id !== training.id || value.problem_pid !== training.pid)) throw new Error("这条提交不属于当前训练题目。");
      if (expectedPid && value.problem_pid !== expectedPid) throw new Error("保存的提交不属于当前题目，已忽略该恢复记录。");
      const restoredLanguage = isLanguage(value.language) ? value.language : DEFAULT_LANGUAGE;
      setPid(value.problem_pid); setSavedCode(value.code); setSavedLanguage(restoredLanguage);
      // 自动恢复执行记录时保留尚未提交的草稿；明确打开历史记录时载入原代码。
      if (!expectedPid) {
        setLanguage(restoredLanguage); setCode(value.code);
        writeLocal(draftKey(value.problem_pid, restoredLanguage), value.code);
        writeLocal(languageKey(value.problem_pid), restoredLanguage);
      }
      writeLocal(lastSubmissionKey(storageId, value.problem_pid), String(value.id));
      writeLocal("oi-selected-problem:" + userId, value.problem_pid);
      setSub(value); setDiag(initialDiagnosis(value)); setDiagError("");
      const activeHint = parsePendingId(readLocal(activeHintKey(storageId, value.problem_pid)));
      const sourceId = activeHint ?? (readLocal(hintSessionKey(userId, value.id)) ? value.id : null);
      setTutorSubmission(sourceId); setLatestSubmitted(null);
      if (submissionPhase(value) === "pending") {
        const stored = parsePendingId(readLocal(pendingKey(storageId, value.problem_pid)));
        if (stored === value.id) setTutorSubmission(parsePendingId(readLocal(pendingHintKey(value.problem_pid))) ?? sourceId);
        void watchTask(value.id, value.problem_pid, generation);
      } else if (submissionPhase(value) === "failed" || submissionPhase(value) === "cancelled") {
        setPendingId(value.id);
      } else {
        if (sourceId && sourceId !== value.id) setLatestSubmitted(value);
        await loadEvidence(value.id);
      }
    }).catch((e) => {
      if (generation !== workRequest.current) return;
      if (e instanceof ApiError && e.status === 404 && expectedPid) {
        removeLocal(lastSubmissionKey(storageId, expectedPid));
        removeLocal(activeHintKey(storageId, expectedPid));
      } else if (!(e instanceof Error && e.name === "AbortError")) setError(messageOf(e));
    }).finally(() => {
      if (generation === workRequest.current) { setBusy(null); requestLock.current = false; }
    });
    return () => {
      if (generation === workRequest.current) workRequest.current += 1;
      controller.abort();
    };
  }

  useEffect(() => {
    if (!restoreTarget) return;
    return restoreSubmission(restoreTarget.id);
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
    if (requestLock.current || !detail || !code.trim() || (sub && submissionPhase(sub) === "pending")) return;
    if (sub && unchangedTutorCode) {
      setError(`代码和语言与提交 #${sub.id} 一致，请先修改上方代码再提交。`);
      editCode();
      return;
    }
    stopWatching();
    const generation = ++workRequest.current;
    evidenceRequest.current += 1;
    requestLock.current = true; setBusy("judge"); setError(""); setDiagError("");
    setSub(null); setDiag(null); setEvidence([]); setEvidenceError("");
    const submittedCode = code;
    const submittedLanguage = language;
    const parentId = sub?.id;
    const controller = new AbortController();
    submitController.current?.abort(); submitController.current = controller;
    try {
      const value = await api.submitAsync(pid, submittedCode, submittedLanguage, parentId, controller.signal, training?.id ?? sub?.training_id ?? undefined, detail.version_id);
      if (generation !== workRequest.current) return;
      setSub(value); setSavedCode(submittedCode); setSavedLanguage(submittedLanguage); setDiag(null);
      writeLocal(lastSubmissionKey(storageId, pid), String(value.id));
      if (submissionPhase(value) === "complete") await finishSubmission(value, pid, generation);
      else {
        setLatestSubmitted(null);
        if (tutorSubmission) writeLocal(pendingHintKey(pid), String(tutorSubmission));
        else removeLocal(pendingHintKey(pid));
        if (submissionPhase(value) === "pending") void watchTask(value.id, pid, generation);
      }
    } catch (e) {
      if (generation === workRequest.current) {
        setSub(sub); setDiag(diag); setEvidence(evidence);
        setError(messageOf(e));
      }
    } finally {
      if (generation === workRequest.current) { setBusy(null); requestLock.current = false; }
    }
  }

  async function retryTask() {
    if (!sub || !sub.job?.retryable || requestLock.current) return;
    const generation = ++workRequest.current;
    requestLock.current = true; setBusy("judge"); setTaskError("");
    const controller = new AbortController();
    submitController.current?.abort(); submitController.current = controller;
    try {
      const value = await api.retrySubmission(sub.id, controller.signal);
      if (generation !== workRequest.current) return;
      setSub(value); setDiag(null); setEvidence([]);
      void watchTask(value.id, pid, generation);
    } catch (e) { if (generation === workRequest.current) setTaskError(messageOf(e)); }
    finally { if (generation === workRequest.current) { requestLock.current = false; setBusy(null); } }
  }

  async function cancelTask() {
    if (!sub?.job?.cancelable || requestLock.current) return;
    const generation = ++workRequest.current;
    taskController.current?.abort(); taskController.current = null;
    setPolling(false); setTaskError(""); setBusy("cancel"); requestLock.current = true;
    const controller = new AbortController();
    submitController.current?.abort(); submitController.current = controller;
    try {
      const result = await cancelAndRefresh(sub.id, {
        signal: controller.signal, cancel: api.cancelSubmission, fetchRecord: api.getSubmission,
      });
      if (generation !== workRequest.current) return;
      setSub(result.record); setSavedCode(result.record.code ?? "");
      setSavedLanguage(isLanguage(result.record.language) ? result.record.language : DEFAULT_LANGUAGE);
      if (result.confirmed) {
        setDiag(null); setEvidence([]); setLatestSubmitted(null);
        writeLocal(pendingKey(storageId, pid), String(result.record.id));
        removeLocal(pendingHintKey(pid));
      } else if (submissionPhase(result.record) === "complete") await finishSubmission(result.record, pid, generation);
      else if (submissionPhase(result.record) === "pending") {
        void watchTask(result.record.id, pid, generation);
        setTaskError("取消尚未确认，服务器任务仍在处理中。请依据最新状态操作。");
      } else setTaskError("该任务已经结束，未执行取消。请查看最新状态。");
    } catch (e) {
      if (generation === workRequest.current) {
        void watchTask(sub.id, pid, generation);
        setTaskError("尚未确认取消结果：" + messageOf(e));
      }
    } finally { if (generation === workRequest.current) { requestLock.current = false; setBusy(null); } }
  }

  function editCode() {
    editorRef.current?.focus();
    editorRef.current?.scrollIntoView({ block: "center" });
  }

  const hasChanged = sub !== null && !sameSource(code, language, savedCode, savedLanguage);
  const phase = sub ? submissionPhase(sub) : null;
  const unchangedTutorCode = phase === "complete" && tutorSubmission !== null && !hasChanged;
  const canTutor = sub && phase === "complete" && diag;

  return (
    <>
      <div className="page-head"><div><h1>{training ? training.title : "练习与诊断"}</h1><p>先看执行证据，再检查代码；修改后重新提交验证。</p></div>
        <label className="problem-picker"><span className="dim">选择题目</span><select aria-label="选择题目" value={pid} disabled={!!busy || loadingProblems || !!training} onChange={(e) => selectProblem(e.target.value)}>
          {!pid && <option value="">{loadingProblems ? "正在加载题目…" : "暂无可用题目"}</option>}
          {pid && !problems.some((p) => p.pid === pid) && <option value={pid}>{pid} · {training ? "训练固定版本" : "历史提交版本"}</option>}
          {problems.map((problem) => <option key={problem.pid} value={problem.pid}>{problem.pid + " · " + (problem.pid === pid && detail ? detail.title : problem.title)}</option>)}
        </select></label>
      </div>
      {!training && sub && <p className="muted">当前继续提交 #{sub.id} 所用的题目版本。另开新练习会保留编辑器代码，与原答疑和提交关系分开。<button className="ghost" disabled={!!busy || phase === "pending" || !problems.some((p) => p.pid === pid)} onClick={startCurrentPractice}>使用当前上架版本开始新练习</button></p>}
      <ErrorNotice message={problemError} onRetry={() => void loadProblems()} />
      {!loadingProblems && !problemError && !problems.length && !pid && <div className="card empty">题库暂无上架题目。原训练和历史记录仍可继续；请联系教师或管理员核对题目。</div>}
      <ErrorNotice message={error} />
      {busy === "restore" && <p role="status">正在恢复提交记录…</p>}
      {pid && <div className="oj">
        <section className="card oj-statement" aria-label="题目内容">
          {loadingDetail && <div className="empty" role="status">正在加载题面…</div>}
          <ErrorNotice message={detailError} onRetry={() => setDetailRetry((value) => value + 1)} />
          {detail && <>
            <div className="oj-title"><h2>{detail.pid + " " + detail.title}</h2><div className="row">
              {!!detail.revision && <span className="badge">题目 v{detail.revision}{training ? " · 训练固定" : sub ? " · 历史提交" : ""}</span>}
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
            <div className="card-head editor-head"><label htmlFor="solution-code"><strong>你的代码</strong></label><div className="row"><label className="language-picker"><span className="sr-only">编程语言</span><select aria-label="编程语言" value={language} disabled={!!busy || phase === "pending" || (!sub && pendingId !== null)} onChange={(e) => selectLanguage(e.target.value as SubmissionLanguage)}>
              {LANGUAGES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select></label><span className="sub">Ctrl / ⌘ + Enter 提交</span></div></div>
            <textarea id="solution-code" ref={editorRef} aria-describedby="code-submission-note" className="code" value={code} spellCheck={false} readOnly={busy === "restore"} onChange={(e) => { setCode(e.target.value); writeLocal(draftKey(pid, language), e.target.value); }} onKeyDown={(e) => {
              if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229 || e.currentTarget.readOnly) return;
              if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); void run(); }
              else if (e.key === "Enter" && !e.altKey) {
                e.preventDefault();
                const editor = e.currentTarget;
                const next = indentedNewline(code, editor.selectionStart, editor.selectionEnd, language);
                setCode(next.value); writeLocal(draftKey(pid, language), next.value);
                requestAnimationFrame(() => editor.setSelectionRange(next.cursor, next.cursor));
              }
            }} />
            <div className="row editor-actions"><button className="primary" onClick={() => void run()} disabled={!!busy || phase === "pending" || (!sub && pendingId !== null) || !detail || !code.trim() || unchangedTutorCode}>
              {busy === "judge" ? "正在发送任务…" : busy === "cancel" ? "正在核对取消…" : busy === "diagnose" ? "正在分析证据…" : busy === "restore" ? "正在恢复…" : phase === "pending" ? "当前任务仍在处理" : sub ? "重新提交验证" : "提交并验证"}
            </button><span className="dim">草稿保留在当前浏览器</span></div>
            <p id="code-submission-note" className="scope-note">{hasChanged ? "代码或语言已有修改。下方结果对应上次提交，请重新提交验证改动。" : unchangedTutorCode ? `代码和语言与提交 #${sub!.id} 一致。修改上方代码后，才能核验这轮答疑中的修改。` : "请在上方编辑器填写代码，聊天中粘贴的代码不会自动替换这里的内容。"}</p>
          </div>
          {pendingId && !sub && <section className="card" aria-label="恢复判题任务"><h2>提交 #{pendingId}</h2><p role="status">{polling ? "正在读取服务器保存的任务状态…" : "状态查询已暂停，代码草稿和服务器任务均已保留。"}</p><ErrorNotice message={taskError} onRetry={() => void watchTask(pendingId, pid, workRequest.current)} /></section>}
          {sub && <section className="card" aria-label="本次执行结果" aria-live="polite">
            <div className="card-head"><h2>提交 #{sub.id} · {LANGUAGE_LABEL[sub.language] ?? sub.language ?? "C++17"}</h2>{phase === "complete" && <CardDownload id={sub.id} />}</div>
            {phase !== "complete" && <div className="task-status" role="status">
              <h3>{phase === "cancelled" ? "已取消" : phase === "failed" ? "判题未完成" : taskLabel(sub)}</h3>
              <p>{phase === "cancelled" ? "取消已记录，后台执行将在下一次状态检查时停止。代码和历史已保留；重新提交会创建新的任务。" : phase === "failed" ? sub.job?.last_error || "执行未能完成，不能将此结果算作学生代码错误。" : "任务已保存在服务器。刷新页面或切换题目不会取消判题，也可从提交记录继续查看。"}</p>
              {sub.job && <p className="dim">已尝试 {sub.job.attempts} / {sub.job.max_attempts} 次{phase === "pending" && !polling ? " · 自动查询已暂停" : ""}</p>}
              <ErrorNotice message={taskError} />
              {phase === "pending" && !polling && <button className="ghost" disabled={!!busy} onClick={() => void watchTask(sub.id, pid, workRequest.current)}>继续查询任务状态</button>}
              {phase === "pending" && sub.job?.cancelable && <button className="ghost" disabled={!!busy} onClick={() => void cancelTask()}>{busy === "cancel" ? "正在核对取消…" : "取消本次任务"}</button>}
              {phase === "failed" && sub.job?.retryable && <button className="ghost" disabled={!!busy} onClick={() => void retryTask()}>重试原代码</button>}
              {phase === "failed" && <p className="dim">重试使用这条提交保存的代码；若要验证编辑后的代码，请选择「重新提交验证」。</p>}
            </div>}
            {phase === "complete" && <>
            <div className="verdict-bar"><VerdictBadge verdict={sub.verdict} tests={sub.tests_passed} />
              {sub.tests_passed && sub.tests_passed.total > 0 && <span className="muted">{sub.tests_passed.scope === "package" ? "题包测试" : sub.tests_passed.scope === "samples" ? "样例测试" : "历史测试"} {sub.tests_passed.passed} / {sub.tests_passed.total} 通过</span>}
            </div>
            <ReviewHistoryPanel key={sub.id} submissionId={sub.id} pending={diag?.status === "needs_human"} />
            <p className="scope-note">{sub.verdict === "CE" ? "编译未通过，尚未运行测试。请先检查下方编译器信息。"
              : sub.verdict === "SAMPLE_PASS" ? "当前仅通过题面样例，尚未验证其他输入。请继续检查边界情况；这里不会把样例通过记为完整解决。"
              : sub.verdict === "NO_TESTS" ? "编译已完成，但没有可运行的测试数据，无法判断程序结果是否正确。"
              : sub.tests_passed?.scope === "package" ? "结果基于本次固定版本的题包测试数据，不代表对所有可能输入的数学证明。"
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
            {canTutor && !tutorSubmission && <button className="ghost" onClick={() => {
              writeLocal(activeHintKey(storageId, pid), String(sub.id));
              setTutorSubmission(sub.id);
            }}>讲解已提交的代码</button>}
            </>}
            {phase !== "pending" && <SubmissionLineage key={sub.id} submissionId={sub.id} onOpen={(id) => setLineageRestore({ id, tick: Date.now() })} />}
          </section>}
          {tutorSubmission && <TutorPanel key={tutorSubmission} submissionId={tutorSubmission} userId={userId} latestSubmitted={latestSubmitted} onEditCode={editCode} />}
        </section>
      </div>}
    </>
  );
}

type ChatMessage = { role: "tutor" | "student"; level: number; text: string };

function TutorPanel({ submissionId, userId, latestSubmitted, onEditCode }: { submissionId: number; userId: number; latestSubmitted: SubmissionResult | null; onEditCode: () => void }) {
  const draftStore = questionDraftStore({ getItem: readLocal, setItem: writeLocal, removeItem: removeLocal }, userId, submissionId);
  const [session, setSession] = useState<SessionReply | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState(() => draftStore.read());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [verifyRetry, setVerifyRetry] = useState(0);
  const [verifyError, setVerifyError] = useState("");
  const [verifyAction, setVerifyAction] = useState<"retry" | "edit" | "restart">("retry");
  const [verifying, setVerifying] = useState(false);
  const sendLock = useRef(false);
  const verified = useRef(new Set<number>());
  const chatRef = useRef<HTMLDivElement>(null);
  const sessionKey = hintSessionKey(userId, submissionId);

  useEffect(() => {
    let active = true;
    setLoading(true); setError("");
    async function open() {
      const stored = Number(readLocal(sessionKey));
      if (stored > 0) {
        try {
          const history = await api.getSession(stored);
          if (!active) return;
          if (history.verified_submission_id) verified.current.add(history.verified_submission_id);
          setSession({ ...history, reply: "", action: "hint" });
          setMessages(history.turns.map((turn) => ({ role: turn.role, level: turn.level, text: turn.content })));
          return;
        } catch (e) {
          if (!(e instanceof ApiError) || e.status !== 404) throw e;
          removeLocal(sessionKey);
        }
      }
      const reply = await api.createSession(submissionId);
      if (!active) return;
      writeLocal(sessionKey, String(reply.session_id));
      setSession(reply);
      setMessages([{ role: "tutor", level: reply.level, text: reply.reply }]);
    }
    void open().catch((e) => { if (active) setError(messageOf(e)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [submissionId, retry]);

  useEffect(() => {
    const chat = chatRef.current;
    if (!chat) return;
    const latest = chat.lastElementChild;
    if (messages[messages.length - 1]?.role === "tutor" && latest) {
      chat.scrollTop += latest.getBoundingClientRect().top - chat.getBoundingClientRect().top;
    } else chat.scrollTop = chat.scrollHeight;
  }, [messages]);

  useEffect(() => {
    if (!session || !latestSubmitted || busy || latestSubmitted.id === submissionId || verified.current.has(latestSubmitted.id)) return;
    let active = true;
    setVerifying(true); setVerifyError(""); setVerifyAction("retry");
    verifyWithRefresh({
      verify: () => api.verifySession(session.session_id, latestSubmitted.id),
      refresh: () => api.getSession(session.session_id),
    }).then((result) => {
      if (!active) return;
      if (!result.reply) {
        setVerifyError(messageOf(result.error));
        const action = verificationRecovery(result.error);
        setVerifyAction(action);
        if (action !== "retry") verified.current.add(latestSubmitted.id);
        setSession((previous) => previous ? { ...previous,
          status: result.history?.status ?? "unavailable",
          verified_submission_id: result.history ? result.history.verified_submission_id : previous.verified_submission_id,
        } : previous);
        return;
      }
      const reply = result.reply;
      if (!active) return;
      verified.current.add(latestSubmitted.id);
      setSession(reply);
      setMessages((rows) => [...rows, { role: "tutor", level: reply.level, text: reply.reply }]);
    }).catch((e) => { if (active) setVerifyError(messageOf(e)); }).finally(() => { if (active) setVerifying(false); });
    return () => { active = false; };
  }, [latestSubmitted?.id, session?.session_id, verifyRetry, busy]);

  async function send() {
    if (!session || !draft.content.trim() || sendLock.current || verifying || !["open", "resolved"].includes(session.status)) return;
    const content = draft.content.trim(), sentId = draft.id;
    sendLock.current = true; setBusy(true); setError("");
    try {
      const reply = await api.sendMessage(session.session_id, content);
      setMessages((rows) => [...rows, { role: "student", level: session.level, text: content }, { role: "tutor", level: reply.level, text: reply.reply }]);
      setSession(reply); draftStore.acknowledge(sentId);
      setDraft((current) => current.id === sentId ? emptyQuestionDraft : current);
    } catch (e) { setError(messageOf(e)); } finally { setBusy(false); sendLock.current = false; }
  }

  function restart() {
    removeLocal(sessionKey);
    verified.current.clear();
    setSession(null); setMessages([]); setError(""); setVerifyError("");
    setRetry((value) => value + 1);
  }

  return <section className="card" aria-label="代码讲解与修改验证">
    <div className="card-head"><h2>代码讲解与修改验证</h2><span className="sub">当前代码 #{session?.verified_submission_id ?? submissionId}</span></div>
    <p className="muted">直接结合已提交的代码和执行记录讲解。没懂可以继续问，老师会逐句解释；修改编辑器内容后请重新提交验证。</p>
    {loading && <p role="status">正在读取代码并准备讲解…</p>}
    <ErrorNotice message={error} onRetry={!session ? () => setRetry((value) => value + 1) : undefined} />
    <ErrorNotice message={verifyError ? "本次判题结果已保存。" + verifyError : ""} onRetry={verifyAction === "retry" ? () => setVerifyRetry((value) => value + 1) : undefined} />
    {verifyError && verifyAction === "edit" && <button className="ghost" onClick={onEditCode}>去修改代码</button>}
    {verifyError && verifyAction === "restart" && <button className="ghost" onClick={restart}>重新开启提示</button>}
    {verifying && <p role="status">正在用新提交核对修改结果…</p>}
    {session && <>
      <div className="chat" ref={chatRef} role="log" aria-label="提示记录" aria-live="polite">
        {messages.map((message, index) => <div key={index} className={"bubble " + message.role}><span className="lvl">{message.role === "tutor" ? "代码讲解" : "我的问题"}</span>{message.role === "tutor" ? <Markdown text={message.text} /> : message.text}</div>)}
      </div>
      {session.status === "resolved" && <p className="scope-note" role="status">提交 #{session.verified_submission_id} 已通过当前题包测试，仍可继续提问代码原理。</p>}
      {["open", "resolved"].includes(session.status) ? <form className="composer" onSubmit={(e) => { e.preventDefault(); void send(); }}>
        <label className="sr-only" htmlFor={"hint-message-" + submissionId}>你的思路或问题</label>
        <textarea id={"hint-message-" + submissionId} className="text grow" rows={2} maxLength={4000} placeholder="Enter 发送，Shift＋Enter 换行。例如：请逐句讲。" aria-describedby={"chat-keys-" + submissionId} value={draft.content} onChange={(e) => setDraft(draftStore.write(e.target.value))} onKeyDown={(e) => {
          if (shouldSendMessage({ key: e.key, shiftKey: e.shiftKey, isComposing: e.nativeEvent.isComposing, keyCode: e.nativeEvent.keyCode })) {
            e.preventDefault(); void send();
          }
        }} disabled={busy || verifying} />
        <button type="submit" className="primary" disabled={!draft.content.trim() || busy || verifying}>{busy ? "正在讲解…" : "提问"}</button>
      </form> : <div className="row recovery-actions"><p className="scope-note" role="status">{session.status === "unverified" ? "历史核验结果待核对，请重新提交正式测试后新建答疑。" : session.status === "unavailable" ? "暂时无法确认核验状态，请重新查询；现有记录已保留。" : "本次提示已暂停，可以保留现有记录并重新开启一轮提示。"}</p><button className="ghost" onClick={restart}>重新开启提示</button></div>}
      {["open", "resolved"].includes(session.status) && <p id={"chat-keys-" + submissionId} className="dim">Enter 发送 · Shift＋Enter 换行 · 未发送的问题保存在当前浏览器</p>}
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
        {rows.map((row) => {
          const recordPhase = submissionPhase({ stage: row.stage ?? "READY", verdict: row.verdict });
          return <tr key={row.id}>
            <td><strong>{row.problem_pid || "题目记录"} {row.problem_title || ""}</strong><div className="dim">#{row.id} · {LANGUAGE_LABEL[row.language] ?? row.language ?? "C++17"}{row.parent_submission_id ? " · 修改自 #" + row.parent_submission_id : ""}{row.created_at ? " · " + new Date(row.created_at).toLocaleString("zh-CN") : ""}</div></td>
            <td>{recordPhase === "complete" ? <VerdictBadge verdict={row.verdict} tests={row.tests_passed} /> : <span className="badge warn">{recordPhase === "cancelled" ? "已取消" : recordPhase === "failed" ? "判题未完成" : taskLabel({ stage: row.stage ?? "QUEUED" })}</span>}</td>
            <td className="muted">{row.diagnosis_status === "needs_human" ? <span className="badge warn">{row.reviewer_id ? "教师已查看 · 仍待核对" : "待教师复核"}</span> : row.reviewer_id ? <span className="badge">教师已复核</span> : null}<p>{recordPhase === "complete" ? row.narrative || "尚无诊断，打开后可查看执行记录。" : recordPhase === "cancelled" ? "任务已取消，代码与历史保留，可打开后重新提交。" : recordPhase === "failed" ? "验证未完成，打开后可查看失败说明或重试。" : "任务仍在处理，打开后继续查看真实状态。"}</p></td>
            <td><div className="row"><button className="ghost" onClick={() => onContinue(row.id)}>打开并继续</button>{recordPhase === "complete" && <CardDownload id={row.id} />}</div></td>
          </tr>;
        })}
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
    <JudgingQueuePanel />
    <ErrorNotice message={error} onRetry={() => void load()} />
    <div className="card">{loading ? <div className="empty" role="status">正在加载待复核记录…</div> : !queue.length && !error ? <div className="empty">暂无待复核记录。已打开记录的审定历史仍保留在下方。</div> : <div className="table-scroll"><table><thead><tr><th>学生 / 题目</th><th>执行结果</th><th>待检查的解释</th><th>操作</th></tr></thead><tbody>
      {queue.map((row) => <tr key={row.diagnosis_id}><td><strong>{row.student_name || "学生提交"}</strong><div className="dim">{row.problem_pid} {row.problem_title} · #{row.submission_id}</div></td><td><VerdictBadge verdict={row.verdict} tests={row.tests_passed} /></td><td className="muted">{row.narrative || "暂无诊断摘要"}</td><td><button className="ghost" onClick={() => { setSelectedId(row.submission_id); writeLocal("oi-selected-review:" + userId, String(row.submission_id)); }}>{selectedId === row.submission_id ? "正在查看" : "查看并复核"}</button></td></tr>)}
    </tbody></table></div>}</div>
    {selectedId && <ReviewPanel key={selectedId} submissionId={selectedId} onReviewed={() => void load()} />}
  </>;
}
