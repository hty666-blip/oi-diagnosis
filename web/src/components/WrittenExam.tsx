import { useEffect, useRef, useState } from "react";
import {
  api, ApiError, messageOf, type WrittenExam, type WrittenExamQuestion,
  type WrittenExamSummary, type WrittenResponse,
} from "../api";
import Markdown from "../Markdown";
import Modal from "./Modal";
import { ErrorNotice } from "./RecordUI";
import { answerText, hasAnswer, useWrittenImages, WrittenAnswers, writtenTypeLabel } from "./WrittenPracticeQuestion";

function sameAnswer(left: WrittenResponse | null, right: WrittenResponse | null) {
  if (left && right && "option_ids" in left && "option_ids" in right) {
    return left.kind === right.kind && [...left.option_ids].sort().join(",") === [...right.option_ids].sort().join(",");
  }
  return JSON.stringify(left) === JSON.stringify(right);
}

function ExamQuestion({ value, draft, disabled, onChange }: {
  value: WrittenExamQuestion;
  draft: WrittenResponse | null;
  disabled: boolean;
  onChange: (response: WrittenResponse | null) => void;
}) {
  const { question, card, answer, explanation } = value;
  const images = useWrittenImages(question);
  const imageSource = (path: string) => images.files.get(path);
  const material = [question.shared_stem, question.stem, ...question.options.map((option) => option.text), explanation].join("\n");
  const figurePaths = new Set([...material.matchAll(/!\[[^\]\n]*\]\((assets\/[^)\n]+)\)/g)].map((match) => match[1]));
  return <div className="written-practice-paper">
    <article className="card written-paper-content">
      <div className="card-head"><h2>第 {card.number} 题</h2><span className="sub">{writtenTypeLabel[question.question_type]} · {card.max_score} 分</span></div>
      {question.shared_stem && <div className="written-shared"><Markdown text={question.shared_stem} imageSource={imageSource} /></div>}
      <Markdown text={question.stem} imageSource={imageSource} />
      {answer && question.options.map((option) => <div key={option.id} className="written-option">
        <strong>{option.id}.</strong><Markdown text={option.text} imageSource={imageSource} />
      </div>)}
      {[...images.files].filter(([path]) => !figurePaths.has(path)).map(([path, url], index) =>
        <figure key={path}><img className="written-asset" src={url} alt={"题图 " + (index + 1)} /></figure>)}
      {images.loading && <p role="status">正在加载题图…</p>}
      {images.error && <ErrorNotice message={images.error} onRetry={images.retry} />}
    </article>
    <section className="card written-paper-answer">
      {answer ? <>
        <div className="card-head"><h2>作答结果</h2><strong>{card.earned_score} / {card.max_score} 分</strong></div>
        <dl className="written-result-fields">
          <div><dt>我的答案</dt><dd>{value.response ? answerText(value.response) : "未作答"}</dd></div>
          <div><dt>参考答案</dt><dd>{answerText(answer)}</dd></div>
        </dl>
        {explanation && <><h3>解析</h3><Markdown text={explanation} imageSource={imageSource} /></>}
      </> : <>
        <fieldset className="written-answer-form" disabled={disabled}>
          <legend>作答</legend>
          <WrittenAnswers question={question} response={draft} imageSource={imageSource} onAnswer={onChange} />
          {draft && <button className="ghost" type="button" onClick={() => onChange(null)}>清空本题</button>}
        </fieldset>
      </>}
    </section>
  </div>;
}

export default function WrittenExamView({ initial, onBack }: { initial: WrittenExam; onBack: () => void }) {
  const [exam, setExam] = useState(initial);
  const [selected, setSelected] = useState(initial.items[0]?.question_id);
  const [question, setQuestion] = useState<WrittenExamQuestion>();
  const [draft, setDraft] = useState<WrittenResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [reload, setReload] = useState(0);
  const [closedByServer, setClosedByServer] = useState(false);
  const [unsavedAtEnd, setUnsavedAtEnd] = useState(false);
  const [clockOrigin] = useState(() => performance.now());
  const startMs = Date.parse(initial.deadline) - Date.parse(initial.server_time);
  const [remaining, setRemaining] = useState(() => Math.max(0, Math.ceil(startMs / 1000)));
  const lifetime = useRef<AbortController>();
  const automatic = useRef(false);
  const working = exam.status === "working" && remaining > 0 && !closedByServer;
  const finished = exam.status === "submitted";
  const normalized = question && hasAnswer(draft, question.question.question_type) ? draft : null;
  const dirty = Boolean(question && !sameAnswer(normalized, question.response));

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (initial.status !== "working") return;
    const update = () => setRemaining(Math.max(0, Math.ceil((startMs - (performance.now() - clockOrigin)) / 1000)));
    const timer = window.setInterval(update, 250);
    return () => window.clearInterval(timer);
  }, [clockOrigin, initial.status, startMs]);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    setLoading(true);
    setQuestion(undefined);
    setLoadError("");
    setSaveError("");
    setConflict(false);
    void api.writtenExamQuestion(exam.id, selected, controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      setQuestion(value);
      setDraft(value.response);
      setExam((current) => ({ ...current, items: current.items.map((item) => item.question_id === selected ? value.card : item) }));
    }).catch((failure) => {
      if (!controller.signal.aborted) setLoadError(messageOf(failure));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [exam.id, selected, finished, reload]);

  useEffect(() => {
    if (!question || !dirty || !working || saving || submitting || saveError) return;
    const timer = window.setTimeout(() => {
      const signal = lifetime.current?.signal;
      setSaving(true);
      void api.saveWrittenExamAnswer(exam.id, question.card.question_id, question.card.revision, normalized, signal)
        .then((card) => {
          if (signal?.aborted) return;
          setQuestion({ ...question, response: normalized, card });
          setExam((current) => ({ ...current, items: current.items.map((item) => item.question_id === card.question_id ? card : item) }));
        }).catch((failure) => {
          if (signal?.aborted) return;
          if (failure instanceof ApiError && failure.code === "written_exam_closed") setClosedByServer(true);
          else {
            setConflict(failure instanceof ApiError && failure.code === "written_exam_draft_changed");
            setSaveError(messageOf(failure));
          }
        }).finally(() => { if (!signal?.aborted) setSaving(false); });
    }, 350);
    return () => window.clearTimeout(timer);
  }, [exam.id, question, dirty, normalized, working, saving, submitting, saveError]);

  useEffect(() => {
    if (!dirty || !working) return;
    function warn(event: BeforeUnloadEvent) { event.preventDefault(); }
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, working]);

  async function submit() {
    const signal = lifetime.current?.signal;
    setSubmitError("");
    setSubmitting(true);
    try {
      const result = await api.submitWrittenExam(exam.id, signal);
      if (signal?.aborted) return;
      setUnsavedAtEnd(dirty);
      setExam(result);
      setConfirm(false);
    } catch (failure) {
      if (!signal?.aborted) setSubmitError(messageOf(failure));
    } finally {
      if (!signal?.aborted) setSubmitting(false);
    }
  }

  useEffect(() => {
    if (working || finished || saving || submitting || automatic.current) return;
    const timer = window.setTimeout(() => {
      automatic.current = true;
      void submit();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [working, finished, saving, submitting]);

  const answered = exam.items.filter((item) => item.answered).length;
  const minutes = Math.floor(remaining / 60);
  const countdown = String(minutes).padStart(2, "0") + ":" + String(remaining % 60).padStart(2, "0");
  return <div className="written-practice-list">
    <section className="card">
      <div className="card-head"><h2>{exam.title}</h2>
        <button className="ghost" type="button" disabled={!finished && (dirty || saving || submitting)} onClick={onBack}>返回试卷</button>
      </div>
      <div className="written-practice-actions">
        {finished ? <strong>{exam.earned_score} / {exam.max_score} 分</strong>
          : <strong>{working ? `剩余 ${countdown}` : "模拟时间到"}</strong>}
        <span role="status" className="muted">已答 {answered} / {exam.items.length} 题{working && (dirty || saving) ? " · 有作答待保存" : ""}</span>
        {working && <button className="btn-primary" type="button" disabled={dirty || saving || submitting || loading} onClick={() => setConfirm(true)}>交卷</button>}
        {!working && !finished && <button className="btn-primary" type="button" disabled={saving || submitting} onClick={() => void submit()}>{submitting ? "正在交卷…" : "提交已保存答卷"}</button>}
      </div>
      {finished && unsavedAtEnd && <p role="status">截止时有作答未同步，成绩按已保存的答题卡计算。</p>}
      {submitError && !confirm && <ErrorNotice message={submitError} />}
      <nav className="admin-tabs" aria-label="答题卡">
        {exam.items.map((item) => <button key={item.question_id} className="ghost" type="button" aria-pressed={selected === item.question_id}
          disabled={working && (dirty || saving)} onClick={() => setSelected(item.question_id)}>
          {item.number} · {finished ? `${item.earned_score}/${item.max_score} 分` : item.answered ? "已答" : "未答"}
        </button>)}
      </nav>
      {working && saveError && <ErrorNotice message={saveError} onRetry={() => {
        if (conflict) setReload((value) => value + 1);
        else setSaveError("");
      }} />}
    </section>
    {loading && <p role="status">正在读取题面…</p>}
    {loadError && <ErrorNotice message={loadError} onRetry={() => setReload((value) => value + 1)} />}
    {!loading && question?.card.question_id === selected && question && <ExamQuestion value={question} draft={draft}
      disabled={!working || submitting || conflict} onChange={(response) => { setDraft(response); setSaveError(""); }} />}
    {confirm && working && <Modal title="确认交卷" busy={submitting} error={submitError} onClose={() => { setConfirm(false); setSubmitError(""); }}>
      <p>已答 {answered} 题，未答 {exam.items.length - answered} 题。交卷后可查看成绩和解析。</p>
      <button className="btn-primary" type="button" disabled={submitting} onClick={() => void submit()}>确认交卷</button>
    </Modal>}
  </div>;
}

export function WrittenExamHistory({ onOpen }: { onOpen: (id: number) => void }) {
  const [rows, setRows] = useState<WrittenExamSummary[]>();
  const [cursors, setCursors] = useState<number[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setRows(undefined);
    setError("");
    void api.writtenExams(cursors[cursors.length - 1], controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      setRows(result.items);
      setNext(result.next_before_id);
    }).catch((failure) => { if (!controller.signal.aborted) setError(messageOf(failure)); });
    return () => controller.abort();
  }, [cursors, reload]);
  return <>
    {error ? <ErrorNotice message={error} onRetry={() => setReload((value) => value + 1)} />
      : !rows ? <p role="status">正在读取模拟记录…</p>
        : rows.length === 0 ? <p className="muted">暂无模拟记录。</p> : <div className="table-scroll"><table>
          <thead><tr><th>试卷</th><th>开始时间</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>{rows.map((row) => <tr key={row.id}>
            <td>{row.title}</td><td>{new Date(row.started_at).toLocaleString("zh-CN")}</td>
            <td>{row.status === "submitted" ? "已交卷" : row.status === "expired" ? "已截止" : "进行中"}</td>
            <td><button className="ghost" type="button" onClick={() => onOpen(row.id)}>{row.status === "submitted" ? "查看答卷" : "打开答卷"}</button></td>
          </tr>)}</tbody>
        </table></div>}
    {(cursors.length > 0 || next !== null) && <div className="written-practice-actions">
      <button className="ghost" type="button" disabled={!rows || !cursors.length} onClick={() => setCursors(cursors.slice(0, -1))}>上一页</button>
      <button className="ghost" type="button" disabled={!rows || next === null} onClick={() => { if (next !== null) setCursors([...cursors, next]); }}>下一页</button>
    </div>}
  </>;
}
