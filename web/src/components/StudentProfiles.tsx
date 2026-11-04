import { useEffect, useRef, useState } from "react";
import { api, messageOf, type AuthUser, type StudentProfile } from "../api";
import { ErrorNotice, VerdictBadge } from "./RecordUI";
import ReviewPanel from "./ReviewPanel";
import { FeedbackStatus } from "./TrainingFeedbackPanel";
import type { TrainingPractice } from "./TrainingWorkspace";

export default function StudentProfiles({ user, onTraining, onPractice }: {
  user: AuthUser; onTraining: (id: number) => void; onPractice: (value: TrainingPractice) => void;
}) {
  const teacher = user.role !== "student";
  const [roster, setRoster] = useState<{ id: number; name: string; class_name: string }[]>([]);
  const [selected, setSelected] = useState(teacher ? 0 : user.id);
  const [profile, setProfile] = useState<StudentProfile | null>(null);
  const [codeId, setCodeId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  useEffect(() => {
    let active = true;
    if (teacher) api.trainingRoster().then((rows) => { if (active) { setRoster(rows); setSelected(rows[0]?.id ?? 0); } }).catch((e) => { if (active) setError(messageOf(e)); });
    return () => { active = false; };
  }, [user.id]);
  async function load() {
    if (!selected) return;
    const current = ++generation.current;
    setLoading(true); setError(""); setProfile(null); setCodeId(null);
    try { const value = await api.studentProfile(selected); if (current === generation.current) setProfile(value); }
    catch (e) { if (current === generation.current) setError(messageOf(e)); }
    finally { if (current === generation.current) setLoading(false); }
  }
  useEffect(() => { void load(); return () => { generation.current++; }; }, [selected]);
  async function download() {
    if (exporting || !profile) return;
    setExporting(true); setError("");
    try { await api.downloadStudentProfile(profile.student.id); } catch (e) { setError(messageOf(e)); }
    finally { setExporting(false); }
  }
  return <>
    <div className="page-head"><div><h1>{teacher ? "学生训练档案" : "我的训练档案"}</h1></div><div className="row"><button className="ghost" disabled={loading || !selected} onClick={() => void load()}>刷新档案</button><button className="ghost" disabled={!profile || exporting} onClick={() => void download()}>{exporting ? "正在导出…" : "导出档案"}</button></div></div>
    {teacher && <label className="feedback-student">选择学生<select value={selected} onChange={(e) => setSelected(Number(e.target.value))}>{!roster.length && <option value={0}>暂无本班学生</option>}{roster.map((student) => <option key={student.id} value={student.id}>{student.class_name} · {student.name}</option>)}</select></label>}
    <ErrorNotice message={error} onRetry={selected ? () => void load() : undefined} />
    {loading && <p role="status">正在读取学生训练档案…</p>}
    {profile && <><h2>{profile.student.name} <span className="sub">{profile.student.class_name}</span></h2>
      <p className="muted">最近可见训练 {profile.summary.trainings} 份，必做题完成 {profile.summary.completed} 份，其中补练 {profile.summary.remedial} 份。更新于 {new Date(profile.observed_at).toLocaleString("zh-CN")}。</p>
      {!profile.items.length && <p className="empty">暂无有访问权限的已发布训练。自由练习仍可从提交记录查看。</p>}
      <ol className="student-training-history">{profile.items.map((task) => <li key={task.id}>
        <div className="card-head"><h3>{task.title}</h3><button className="ghost" onClick={() => onTraining(task.id)}>打开训练与反馈</button></div>
        <p className="muted">训练 #{task.id}{task.source_training_id ? ` · 补练来源 #${task.source_training_id}` : ""} · {task.complete ? "必做题已正式通过" : "必做题尚未全部通过"}</p>
        <div className="table-scroll"><table><thead><tr><th>题目</th><th>最新结果</th><th>操作</th></tr></thead><tbody>{task.problems.map((problem) => <tr key={problem.pid}><th scope="row">{problem.pid} · {problem.title}<small>{problem.required ? "必做" : "选做"}</small></th><td>{!problem.cell ? "未提交" : problem.cell.state === "complete" ? <VerdictBadge verdict={problem.cell.verdict} tests={problem.cell.tests_passed} /> : { pending: "判题中", failed: "执行故障", cancelled: "已取消" }[problem.cell.state]}</td>
          <td>{teacher ? problem.cell && <button className="ghost" onClick={() => setCodeId(problem.cell!.id)}>查看代码与证据</button> : <button className="ghost" onClick={() => onPractice({ id: task.id, title: task.title, pid: problem.pid, submissionId: problem.cell?.id })}>继续练习</button>}</td></tr>)}</tbody></table></div>
        {task.latest_feedback && <div className="profile-feedback"><h4>最近个别反馈 · 共 {task.feedback_count} 条</h4><p className="feedback-content">{task.latest_feedback.content}</p><FeedbackStatus feedback={task.latest_feedback} /></div>}
      </li>)}</ol>
      <p className="scope-note">汇总最近 100 份可访问训练。正式通过仅指题包测试通过。{profile.truncated ? "更早记录未包含在本次汇总中。" : ""}</p></>}
    {teacher && codeId && <ReviewPanel key={codeId} submissionId={codeId} onReviewed={() => void load()} />}
  </>;
}
