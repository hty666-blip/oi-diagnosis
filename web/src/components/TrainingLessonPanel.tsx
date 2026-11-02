import { useEffect, useRef, useState } from "react";
import { api, messageOf, type TrainingLesson } from "../api";
import Markdown from "../Markdown";
import { ErrorNotice } from "./RecordUI";

const dateLabel = (value: string) => new Date(value).toLocaleString("zh-CN");
const stateLabels: Record<string, string> = { AC: "正式通过", SAMPLE_PASS: "样例通过", NO_TESTS: "没有测试", UNVERIFIED: "待核对",
  CE: "编译错误", WA: "答案错误", RE: "运行错误", TLE: "超时", MLE: "内存超限", OLE: "输出超限", PE: "格式错误",
  pending: "判题中", failed: "执行故障", cancelled: "已取消", not_submitted: "未提交" };
const sources = { ai: "AI 草稿 · 待教练核对", facts: "事实提纲 · 本轮未取得可用 AI 草稿", manual: "教练编辑稿" };

export default function TrainingLessonPanel({ trainingId, teacher, refreshToken, onEvidence }: {
  trainingId: number; teacher: boolean; refreshToken: string; onEvidence: (id: number) => void;
}) {
  const [lesson, setLesson] = useState<TrainingLesson | null>(null);
  const [text, setText] = useState("");
  const [base, setBase] = useState({ revision: 0, content: "" });
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<"generate" | "save" | "publish" | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const generation = useRef(0);
  const mutation = useRef(false);
  const dirty = text !== base.content;
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;

  function adopt(next: TrainingLesson) {
    const content = next.draft?.content ?? "";
    setText(content); setBase({ revision: next.draft?.revision ?? 0, content });
  }
  async function load() {
    const current = ++generation.current;
    setLoading(true); setError("");
    try {
      const next = await api.trainingLesson(trainingId);
      if (current !== generation.current) return;
      setLesson(next);
      if (!dirtyRef.current) adopt(next);
    } catch (e) { if (current === generation.current) setError(messageOf(e)); }
    finally { if (current === generation.current) setLoading(false); }
  }
  useEffect(() => { void load(); return () => { generation.current++; }; }, [trainingId, refreshToken]);

  async function act(action: "generate" | "save" | "publish") {
    if (mutation.current || !lesson?.analysis || loading) return;
    mutation.current = true; setBusy(action); setError(""); setNotice("");
    const current = generation.current;
    try {
      const next = action === "generate" ? await api.generateTrainingLesson(trainingId, base.revision)
        : action === "save" ? await api.saveTrainingLesson(trainingId, base.revision, text, lesson.analysis.basis_digest)
        : await api.publishTrainingLesson(trainingId, base.revision);
      if (current !== generation.current) return;
      setLesson(next); adopt(next);
      setNotice(action === "publish" ? "讲评已发布，训练名单内学生可以查看。" : action === "generate" ? "新草稿已保存为独立版本，请核对并编辑后发布。" : "讲评草稿已保存，学生仍看到上次发布的版本。");
    } catch (e) { if (current === generation.current) setError(messageOf(e)); }
    finally { mutation.current = false; setBusy(null); }
  }

  const analysis = lesson?.analysis;
  const conflict = base.revision !== (lesson?.draft?.revision ?? 0);
  const disabled = loading || !!busy;
  return <section className="training-lessons" aria-label={teacher ? "学情与讲评备课" : "教练讲评"}>
    <div className="card-head"><h3>{teacher ? "学情与讲评备课" : "教练讲评"}</h3><button className="ghost" disabled={disabled} onClick={() => void load()}>刷新{teacher ? "学情与讲评" : "讲评"}</button></div>
    {loading && <p role="status">正在核对训练记录与讲评版本…</p>}
    <ErrorNotice message={error} />
    {notice && <p className="scope-note" role="status">{notice}</p>}
    {teacher && analysis && <>
      <p className="muted">基于当前可见名单和本次训练最新提交，更新于 {dateLabel(analysis.observed_at)}。人数按学生计，同一学生重复提交不会重复计数。</p>
      <div className="table-scroll"><table><caption className="sr-only">本次训练单题学情</caption><thead><tr><th>题目</th><th>最新状态分布</th><th>修改结果</th></tr></thead><tbody>
        {analysis.problems.map((problem) => <tr key={problem.pid}><th scope="row">{problem.pid}<small>{problem.title}</small></th>
          <td>{Object.entries(problem.counts).map(([state, count]) => <span className="lesson-count" key={state}>{stateLabels[state] ?? state} {count} 人</span>)}</td>
          <td>先前未通过、最新正式通过 {problem.repaired} 人<small>错因尚未分类 {problem.unclassified} 人</small></td></tr>)}
      </tbody></table></div>
      <h4>共性问题与依据</h4>
      {!analysis.issues.length && <p className="muted">当前没有带可核对证据的错因分类。可以从下方案例查看实际记录，不能据此判断全班没有薄弱点。</p>}
      {analysis.issues.map((issue) => <div className="lesson-issue" key={`${issue.pid}:${issue.code}:${issue.source}`}>
        <strong>{issue.pid} · {issue.label} · {issue.students} 人</strong><span className="muted">{issue.source === "teacher" ? "教师已确认" : "自动分析 · 待教练确认"}</span>
        <div className="row">{issue.submission_ids.slice(0, 8).map((id) => <button className="ghost" key={id} onClick={() => onEvidence(id)}>查看提交 #{id}</button>)}</div>
      </div>)}
      <details className="lesson-examples"><summary>备课候选案例 · 最多 6 条</summary>
        {analysis.examples.map((example) => <div key={example.submission_id}><h4>{example.pid} · 提交 #{example.submission_id} · {stateLabels[example.verdict ?? ""] ?? example.verdict}</h4>
          <pre className="review-code"><code>{example.code}</code></pre>{example.code_truncated && <p className="muted">源码仅展示前 1600 字符，完整代码请打开提交。</p>}
          <p>{example.narrative}</p><button className="ghost" onClick={() => onEvidence(example.submission_id)}>查看完整源码与证据</button>
        </div>)}{!analysis.examples.length && <p className="muted">暂无已完成判题的可用案例。</p>}</details>
      <h4>讲评草稿</h4>
      {lesson?.draft && <p className="muted">版本 {lesson.draft.revision} · {sources[lesson.draft.source]} · {dateLabel(lesson.draft.created_at)}</p>}
      {lesson?.draft?.stale && <p className="scope-note" role="status">草稿依据的训练记录已有变化。请核对上方最新学情，再保存或重新生成；旧稿仍保留。</p>}
      {conflict && <p className="scope-note" role="status">服务器已有其他版本。当前输入已保留，可复制内容后载入最新草稿。</p>}
      <label className="sr-only" htmlFor={`lesson-draft-${trainingId}`}>讲评草稿内容</label>
      <textarea id={`lesson-draft-${trainingId}`} className="lesson-editor" rows={14} maxLength={12000} value={text} disabled={disabled} onChange={(e) => setText(e.target.value)} placeholder="可以直接编写讲评，也可以根据训练记录生成草稿。支持 Markdown 和局部代码块。" />
      <p className="muted">发布内容对训练名单内学生可见。请核对解释与案例，避免在公开讲评中填写学生姓名或账号。刷新学情会保留未保存的文字。</p>
      <div className="row lesson-actions">
        <button className="ghost" disabled={disabled || dirty || conflict} onClick={() => void act("generate")}>{busy === "generate" ? "正在生成…" : "生成讲评草稿"}</button>
        <button className="primary" disabled={disabled || !text.trim() || conflict} onClick={() => void act("save")}>{busy === "save" ? "正在保存…" : "保存草稿"}</button>
        <button className="primary" disabled={disabled || dirty || conflict || !lesson?.draft || lesson.draft.stale || lesson.published?.revision === base.revision} onClick={() => void act("publish")}>{busy === "publish" ? "正在发布…" : "发布讲评"}</button>
        {(dirty || conflict) && <button className="ghost" disabled={disabled} onClick={() => lesson && adopt(lesson)}>放弃编辑并载入已保存稿</button>}
      </div>
      {dirty && <p className="muted" role="status">有未保存编辑，请先保存再生成或发布。</p>}
      {!!text.trim() && <details className="lesson-preview"><summary>预览学生将看到的正文</summary><Markdown text={text} /></details>}
      {!!lesson?.history?.length && <details className="lesson-history"><summary>最近 10 个草稿版本</summary>{lesson.history.map((version) => <details key={version.revision}><summary>版本 {version.revision} · {sources[version.source]} · {dateLabel(version.created_at)}</summary><Markdown text={version.content} /></details>)}</details>}
    </>}
    {lesson?.published ? <div className="lesson-published"><h4>{teacher ? "当前学生可见版本" : "已发布讲评"}</h4><p className="muted">版本 {lesson.published.revision} · 发布于 {dateLabel(lesson.published.published_at)}</p><Markdown text={lesson.published.content} /></div>
      : !loading && lesson && <p className="muted">{teacher ? "尚未发布讲评，草稿只对教练可见。" : "教练尚未发布讲评，可以先完成训练并使用代码讲解。"}</p>}
  </section>;
}
