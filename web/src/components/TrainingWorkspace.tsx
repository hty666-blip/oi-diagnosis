import { useEffect, useRef, useState } from "react";
import { api, messageOf, type AuthUser, type Problem, type Training, type TrainingDetail, type TrainingInput } from "../api";
import { ErrorNotice, VerdictBadge } from "./RecordUI";
import ReviewPanel from "./ReviewPanel";
import TrainingLessonPanel from "./TrainingLessonPanel";
import TrainingFeedbackPanel from "./TrainingFeedbackPanel";

export type TrainingPractice = { id: number; pid: string; title: string; submissionId?: number };
const dateLabel = (value: string) => new Date(value).toLocaleString("zh-CN");
const localDate = (value: string) => {
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};
const emptyDraft = (className: string): TrainingInput => ({ title: "", class_name: className, objective: "", description: "",
  due_at: new Date(Date.now() + 7 * 86400000).toISOString(), problems: [] });

export default function TrainingWorkspace({ user, onPractice, initialTrainingId }: { user: AuthUser; onPractice: (value: TrainingPractice) => void; initialTrainingId?: number }) {
  const teacher = user.role !== "student";
  const [items, setItems] = useState<Training[]>([]);
  const [detail, setDetail] = useState<TrainingDetail | null>(null);
  const [problems, setProblems] = useState<Problem[]>([]);
  const [roster, setRoster] = useState<{ id: number; name: string; class_name: string }[]>([]);
  const [draft, setDraft] = useState<TrainingInput | null>(null);
  const [editingId, setEditingId] = useState<number>();
  const [selectedCode, setSelectedCode] = useState<number | null>(null);
  const [feedbackStudent, setFeedbackStudent] = useState<number>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [search, setSearch] = useState("");
  const generation = useRef(0);
  const saveLock = useRef(false);
  const groups = [...new Set(roster.map((s) => s.class_name))];

  async function load(id?: number) {
    const current = ++generation.current;
    setLoading(true); setError(""); setSelectedCode(null); setFeedbackStudent(undefined);
    try {
      const [list, members, bank] = await Promise.all([api.trainings(), teacher ? api.trainingRoster() : Promise.resolve([]), teacher ? api.problems() : Promise.resolve([])]);
      const record = id ? await api.training(id) : null;
      if (current !== generation.current) return;
      setItems(list); setRoster(members); setProblems(bank); setDetail(record);
    } catch (e) { if (current === generation.current) setError(messageOf(e)); }
    finally { if (current === generation.current) setLoading(false); }
  }
  useEffect(() => { void load(initialTrainingId); return () => { generation.current++; }; }, [user.id, initialTrainingId]);

  async function save(publish = false) {
    if (saveLock.current || (!publish && !draft) || (publish && !detail)) return;
    saveLock.current = true; setSaving(true); setError("");
    try {
      const record = publish ? await api.publishTraining(detail!.id) : await api.saveTraining(draft!, editingId);
      setDraft(null); setEditingId(undefined);
      setNotice(publish ? "训练已发布，名单中的学生现在可以进入练习。" : "草稿已保存，请核对题目与截止时间后发布。");
      await load(record.id);
    } catch (e) { setError(messageOf(e)); }
    finally { saveLock.current = false; setSaving(false); }
  }

  function edit(record?: TrainingDetail) {
    setDraft(record ? { title: record.title, class_name: record.class_name, objective: record.objective,
      description: record.description, due_at: record.due_at, student_ids: record.student_ids ?? null, source_training_id: record.source_training_id ?? null, problems: record.problems.map((p) => ({ pid: p.pid, required: p.required })) }
      : emptyDraft(user.class_name || groups[0] || ""));
    setEditingId(record?.id); setError(""); setNotice("");
  }

  function remediate(studentId: number) {
    if (!detail || !teacher) return;
    setDraft({ ...emptyDraft(detail.class_name), title: `${detail.title} · 定向补练`.slice(0, 120),
      objective: detail.objective, description: "根据原训练反馈完成补练，并重新提交验证修改。",
      problems: detail.problems.map((p) => ({ pid: p.pid, required: p.required })), student_ids: [studentId], source_training_id: detail.id });
    setEditingId(undefined); setNotice(""); setError(""); window.scrollTo({ top: 0, behavior: "smooth" });
  }

  return <>
    <div className="page-head"><div><h1>{teacher ? "训练工作台" : "我的训练"}</h1></div>
      <div className="row"><button className="ghost" disabled={loading || saving} onClick={() => void load(detail?.id)}>刷新训练</button>
      {teacher && !draft && <button className="primary" disabled={loading || saving || !groups.length} onClick={() => edit()}>布置训练</button>}</div></div>
    <ErrorNotice message={error} onRetry={draft ? undefined : () => void load(detail?.id)} />
    {notice && <p role="status" className="scope-note">{notice}</p>}
    {loading && <p role="status">正在读取训练与完成记录…</p>}
    {teacher && !loading && <details className="training-roster"><summary>班级名单 · {roster.length} 人</summary>
      {!roster.length ? <p>当前账号没有可用班级名单，请由管理员为学生和教练配置班级后再布置训练。</p> : groups.map((group) => <p key={group}><strong>{group}</strong>：{roster.filter((s) => s.class_name === group).map((s) => s.name).join("、")}</p>)}
      <p className="dim">发布时固定名单；学生转班后不再访问原班训练。</p></details>}
    {draft && <section className="card training-form"><h2>{editingId ? "编辑训练草稿" : "布置新训练"}</h2>
      <form onSubmit={(e) => { e.preventDefault(); void save(); }}><fieldset disabled={saving} className="training-form-fields">
        <div className="training-fields"><label>训练名称<input required maxLength={120} value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} /></label>
        <label>班级<select value={draft.class_name} onChange={(e) => setDraft({ ...draft, class_name: e.target.value, student_ids: null, source_training_id: null })}>{groups.map((g) => <option key={g}>{g}</option>)}</select></label>
        <label>截止时间<input required type="datetime-local" value={localDate(draft.due_at)} onChange={(e) => { if (e.target.value) setDraft({ ...draft, due_at: new Date(e.target.value).toISOString() }); }} /></label></div>
        <label>训练目标<input maxLength={500} value={draft.objective} onChange={(e) => setDraft({ ...draft, objective: e.target.value })} placeholder="例如：理解累计更新，检查整数范围" /></label>
        <label>训练说明<textarea rows={3} maxLength={5000} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></label>
        {draft.source_training_id && <p className="scope-note">补练关联原训练 #{draft.source_training_id}，请核对目标学生和补练题目后发布。</p>}
        <label>布置范围<select value={draft.student_ids == null ? "class" : "selected"} onChange={(e) => setDraft({ ...draft, student_ids: e.target.value === "class" ? null : [] })}>
          {!draft.source_training_id && <option value="class">发布时的全班名单</option>}<option value="selected">指定学生</option></select></label>
        {draft.student_ids != null && <div className="training-audience" role="group" aria-label="指定学生名单">{roster.filter((student) => student.class_name === draft.class_name).map((student) => <label key={student.id}><input type="checkbox" checked={draft.student_ids!.includes(student.id)} onChange={(e) => setDraft({ ...draft, student_ids: e.target.checked ? [...draft.student_ids!, student.id] : draft.student_ids!.filter((id) => id !== student.id) })} />{student.name}</label>)}</div>}
        <h3>已选题目 · {draft.problems.length} / 20</h3>
        {!draft.problems.length && <p className="muted">从下方题库添加题目，至少保留一道必做题。</p>}
        <ol className="training-selected">{draft.problems.map((item, i) => <li key={item.pid}>
          <span>{item.pid} · {problems.find((p) => p.pid === item.pid)?.title}</span>
          <label><input type="checkbox" checked={item.required} onChange={(e) => setDraft({ ...draft, problems: draft.problems.map((p) => p.pid === item.pid ? { ...p, required: e.target.checked } : p) })} />必做</label>
          <button type="button" className="ghost" disabled={!i} aria-label={"上移 " + item.pid} onClick={() => { const next = [...draft.problems]; [next[i - 1], next[i]] = [next[i], next[i - 1]]; setDraft({ ...draft, problems: next }); }}>上移</button>
          <button type="button" className="ghost" onClick={() => setDraft({ ...draft, problems: draft.problems.filter((p) => p.pid !== item.pid) })}>移除</button>
        </li>)}</ol>
        <label>查找题目<input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="题号或标题" /></label>
        <div className="training-bank">{problems.filter((p) => (p.pid + p.title).toLowerCase().includes(search.toLowerCase())).map((p) => <div className="row" key={p.pid}><span>{p.pid} · {p.title} <span className="dim">{p.verification_scope === "package" ? "已配置正式测试" : p.verification_scope === "samples" ? "仅样例，暂不计正式完成" : `题包等级 ${p.package_level}`}</span></span><button type="button" className="ghost" disabled={draft.problems.some((v) => v.pid === p.pid) || draft.problems.length >= 20} onClick={() => setDraft({ ...draft, problems: [...draft.problems, { pid: p.pid, required: true }] })}>添加</button></div>)}</div>
        <p className="scope-note">仅正式测试全部通过计入完成；样例题可用于练习。</p>
        <div className="row"><button className="primary" disabled={saving || !draft.title.trim() || !draft.problems.some((p) => p.required) || draft.student_ids?.length === 0}>{saving ? "正在保存…" : "保存草稿并预览"}</button><button type="button" className="ghost" disabled={saving} onClick={() => setDraft(null)}>取消编辑</button></div>
      </fieldset></form></section>}
    <div className="training-layout" aria-busy={loading}>
      <section className="training-list" aria-label="训练列表"><h2>{teacher ? "训练任务" : "收到的训练"}</h2>
        {!items.length && !loading && <p className="empty">{teacher ? "还没有训练。先选择题目并保存一份草稿。" : "暂未收到训练，可以先到自由练习中选题。"}</p>}
        {items.map((item) => <button key={item.id} className={"training-item " + (item.id === detail?.id ? "selected" : "")} disabled={loading || saving || !!draft} onClick={() => void load(item.id)} aria-pressed={item.id === detail?.id}>
          <strong>{item.title}</strong><span>{item.class_name} · {item.status === "draft" ? "草稿" : new Date(item.due_at).getTime() < Date.now() ? "已截止 · 可补交" : "进行中"}</span><span>截止 {dateLabel(item.due_at)}</span></button>)}
      </section>
      <section className="training-detail" aria-label="训练详情">
        {!detail && !loading && <p className="empty">选择一份训练查看题目和完成情况。</p>}
        {detail && !draft && <><div className="card-head"><h2>{detail.title}</h2>{teacher && detail.status === "draft" && <div className="row"><button className="ghost" disabled={saving || loading} onClick={() => edit(detail)}>编辑草稿</button><button className="primary" disabled={saving || loading} onClick={() => void save(true)}>{saving ? "正在发布…" : "发布到班级"}</button></div>}</div>
          <p className="muted">{detail.class_name} · 截止 {dateLabel(detail.due_at)} · {detail.status === "draft" ? "尚未发布，学生不可见" : "已发布"}</p>
          {detail.source_training_id && <p className="scope-note">补练来源：训练 #{detail.source_training_id}</p>}
          {teacher && detail.status === "draft" && <p className="muted">布置范围：{detail.student_ids == null ? "发布时全班学生" : detail.student_ids.map((id) => roster.find((student) => student.id === id)?.name ?? `学生 #${id}（请核对名单）`).join("、")}</p>}
          {detail.objective && <p><strong>训练目标：</strong>{detail.objective}</p>}
          {detail.description && <p className="training-description">{detail.description}</p>}
          <ol className="training-problems">{detail.problems.map((p, i) => <li key={p.pid}><span><strong>{p.pid} · {p.title}</strong> <span className="dim">{p.required ? "必做" : "选做"}</span></span>{!teacher && <button className="ghost" onClick={() => onPractice({ id: detail.id, pid: p.pid, title: detail.title, submissionId: detail.rows[0]?.cells[i]?.id })}>进入练习</button>}</li>)}</ol>
          {detail.status === "published" && <><h3>{teacher ? "完成矩阵" : "我的完成情况"}</h3>
            <p className="muted">{teacher ? `名单内 ${detail.summary.students} 人，已参与 ${detail.summary.participated} 人，完成全部必做题 ${detail.summary.completed} 人。` : "按本次训练中的最新提交展示。"}补交提交 {detail.summary.late_submissions} 条。</p>
            <div className="table-scroll"><table className="training-matrix"><thead><tr><th>学生</th>{detail.problems.map((p) => <th key={p.pid}>{p.pid}<small>{p.required ? "必做" : "选做"}</small></th>)}</tr></thead><tbody>
              {detail.rows.map((row) => <tr key={row.student_id}><th scope="row">{row.name}{row.complete && <small>必做题已完成</small>}{teacher && <button className="ghost" onClick={() => { setFeedbackStudent(row.student_id); document.getElementById(`training-feedback-${detail.id}`)?.scrollIntoView({ block: "start", behavior: "smooth" }); }}>指导与补练</button>}</th>{row.cells.map((cell, i) => <td key={detail.problems[i].pid}>{!cell ? <span className="dim">未提交</span> : <>
                {cell.state === "complete" ? <VerdictBadge verdict={cell.verdict} tests={cell.tests_passed} /> : <span>{({ pending: "判题中", failed: "执行故障", cancelled: "已取消" })[cell.state]}</span>}
                <small>{cell.attempts} 次提交{cell.late ? " · 补交" : ""}</small>
                <button className="ghost" onClick={() => teacher ? setSelectedCode(cell.id) : onPractice({ id: detail.id, pid: detail.problems[i].pid, title: detail.title, submissionId: cell.id })}>{teacher ? "查看代码与证据" : "继续练习"}</button>
              </>}</td>)}</tr>)}</tbody></table></div>
            {!detail.rows.length && <p className="empty">当前班级没有仍具访问权限的发布名单成员。</p>}
            <p className="scope-note">按本训练最新提交统计，不含自由练习。执行故障单独标记。更新于 {dateLabel(detail.observed_at)}。</p></>}
          {detail.status === "published" && <><TrainingFeedbackPanel key={`feedback-${detail.id}`} detail={detail} user={user} targetStudentId={feedbackStudent} onRemediate={remediate} /><TrainingLessonPanel key={detail.id} trainingId={detail.id} teacher={teacher} refreshToken={detail.observed_at} onEvidence={setSelectedCode} /></>}
        </>}
      </section>
    </div>
    {teacher && selectedCode && <ReviewPanel key={selectedCode} submissionId={selectedCode} onReviewed={() => void load(detail?.id)} />}
  </>;
}
