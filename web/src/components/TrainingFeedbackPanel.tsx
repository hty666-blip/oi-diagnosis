import { useEffect, useRef, useState } from "react";
import { api, messageOf, readLocal, removeLocal, writeLocal, type AuthUser, type FeedbackHistory, type StudentFeedback, type TrainingDetail } from "../api";
import { ErrorNotice } from "./RecordUI";

const dateLabel = (value: string) => new Date(value).toLocaleString("zh-CN");
export function FeedbackStatus({ feedback }: { feedback: StudentFeedback }) {
  return <p className="muted">{feedback.read_at ? "学生已标记查看" : "尚未标记查看"} · {feedback.followup.passed ? "反馈后新提交正式通过" : feedback.followup.has_new_submission ? "反馈后有新提交，尚未正式通过" : "反馈后尚无新提交"}</p>;
}
type Draft = { content: string; requestId: string; submissionId: number | null };
function savedDraft(key: string): Draft {
  try {
    const value = JSON.parse(readLocal(key) ?? "null");
    if (value && typeof value.content === "string" && typeof value.requestId === "string") return value;
  } catch { /* 保留可用输入，不依赖浏览器存储。 */ }
  return { content: "", requestId: crypto.randomUUID(), submissionId: null };
}

export default function TrainingFeedbackPanel({ detail, user, targetStudentId, onRemediate }: {
  detail: TrainingDetail; user: AuthUser; targetStudentId?: number; onRemediate: (studentId: number) => void;
}) {
  const teacher = user.role !== "student";
  const [selected, setSelected] = useState(targetStudentId ?? detail.rows[0]?.student_id ?? user.id);
  const key = `oi-feedback-draft:${user.id}:${detail.id}:${selected}`;
  const [draft, setDraft] = useState<Draft>(() => savedDraft(key));
  const [history, setHistory] = useState<FeedbackHistory | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const mutation = useRef(false);
  const section = useRef<HTMLElement>(null);
  const row = detail.rows.find((student) => student.student_id === selected);
  const sources = row?.cells.flatMap((cell, i) => cell ? [{ id: cell.id, pid: detail.problems[i].pid }] : []) ?? [];

  useEffect(() => {
    if (targetStudentId !== undefined) { setSelected(targetStudentId); section.current?.scrollIntoView({ block: "start", behavior: "smooth" }); }
  }, [targetStudentId]);
  async function load() {
    const current = ++generation.current;
    setError(""); setHistory(null);
    if (!row) { setLoading(false); return; }
    setLoading(true);
    try {
      const value = await api.trainingFeedback(detail.id, selected);
      if (current === generation.current) setHistory(value);
    } catch (e) { if (current === generation.current) setError(messageOf(e)); }
    finally { if (current === generation.current) setLoading(false); }
  }
  useEffect(() => { setDraft(savedDraft(key)); setNotice(""); }, [key]);
  useEffect(() => { void load(); return () => { generation.current++; }; }, [key, detail.observed_at]);
  function edit(next: Partial<Draft>) {
    const value = { ...draft, ...next, requestId: crypto.randomUUID() };
    setDraft(value); writeLocal(key, JSON.stringify(value));
  }
  async function act(readId?: number) {
    if (mutation.current || !row || loading || (!readId && !draft.content.trim())) return;
    mutation.current = true; setBusy(true); setError("");
    const current = generation.current;
    try {
      const value = readId ? await api.markFeedbackRead(detail.id, readId)
        : await api.sendTrainingFeedback(detail.id, selected, draft.content.trim(), draft.requestId, draft.submissionId);
      if (!readId) removeLocal(key);
      if (current !== generation.current) return;
      setHistory(value);
      if (!readId) { setDraft(savedDraft(key)); setNotice("个别反馈已发送，仅该学生和授权教练可见。"); }
    } catch (e) { if (current === generation.current) setError(messageOf(e)); }
    finally { mutation.current = false; setBusy(false); }
  }
  return <section id={`training-feedback-${detail.id}`} className="training-feedback" ref={section} aria-label={teacher ? "个别反馈与补练" : "我的教练反馈"}>
    <div className="card-head"><h3>{teacher ? "个别反馈与补练" : "我的教练反馈"}</h3><button className="ghost" disabled={busy || loading || !row} onClick={() => void load()}>刷新反馈</button></div>
    {teacher && <label className="feedback-student">选择指导学生<select value={selected} disabled={busy || loading} onChange={(e) => setSelected(Number(e.target.value))}>
      {detail.rows.map((student) => <option key={student.student_id} value={student.student_id}>{student.name}</option>)}</select></label>}
    {!row && <p className="muted">当前没有仍在本班训练名单中的学生。</p>}
    <ErrorNotice message={error} />{notice && <p role="status" className="scope-note">{notice}</p>}
    {loading && <p role="status">正在读取该学生的反馈与提交变化…</p>}
    {teacher && row && <form onSubmit={(e) => { e.preventDefault(); void act(); }}>
      <label>反馈依据<select value={draft.submissionId ?? ""} disabled={busy} onChange={(e) => edit({ submissionId: e.target.value ? Number(e.target.value) : null })}>
        <option value="">本次训练整体指导</option>{sources.map((source) => <option key={source.id} value={source.id}>{source.pid} · 提交 #{source.id}</option>)}
        {draft.submissionId !== null && !sources.some((s) => s.id === draft.submissionId) && <option value={draft.submissionId}>已选的较早提交 #{draft.submissionId}</option>}
      </select></label>
      <label>给 {row.name} 的反馈<textarea rows={4} maxLength={4000} value={draft.content} disabled={busy} onChange={(e) => edit({ content: e.target.value })} placeholder="说明观察到的问题、建议修改的位置或下一步练习。" /></label>
      <div className="row"><button className="primary" disabled={busy || loading || !draft.content.trim()}>{busy ? "正在发送…" : "发送个别反馈"}</button><button type="button" className="ghost" disabled={busy} onClick={() => onRemediate(selected)}>为该学生安排补练</button></div>
      <p className="muted">反馈不会改变判题结果。已查看只表示学生明确标记查看；后续通过由新增提交验证。</p>
    </form>}
    {!loading && history && !history.items.length && <p className="muted">暂无个别反馈。</p>}
    <ol className="feedback-history">{history?.items.map((feedback) => <li key={feedback.id}>
      <p className="muted">{feedback.author} · {dateLabel(feedback.created_at)}{feedback.submission_id ? ` · ${feedback.source_pid ?? ""} 提交 #${feedback.submission_id}` : " · 训练整体指导"}</p>
      <p className="feedback-content">{feedback.content}</p><FeedbackStatus feedback={feedback} />
      {!teacher && !feedback.read_at && <button className="ghost" disabled={busy} onClick={() => void act(feedback.id)}>标为已查看</button>}
    </li>)}</ol>
    {history?.truncated && <p className="muted">这里展示最近 50 条反馈。</p>}
  </section>;
}
