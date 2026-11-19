import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  api, ApiError, messageOf, type WrittenExam, type WrittenExamQuestion,
  type WrittenExamReviewItem, type WrittenExamSummary, type WrittenPracticePick, type WrittenResponse,
} from "../api";
import Markdown from "../Markdown";
import { readExamQuestion, rememberExamQuestion } from "../written-practice";
import Modal from "./Modal";
import { ErrorNotice } from "./RecordUI";
import WrittenExamStudy from "./WrittenExamStudy";
import WrittenHelp from "./WrittenHelp";
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

export default function WrittenExamView({ userId, initial, onBack, onAnswerPending, onRetry, onAttempt, onStudyPractice }: {
  userId: number;
  initial: WrittenExam;
  onBack: () => void;
  onAnswerPending: (pending: boolean) => void;
  onRetry: (item: WrittenExamReviewItem) => void;
  onAttempt: (id: number) => void;
  onStudyPractice: (question: WrittenPracticePick) => void;
}) {
  const [exam, setExam] = useState(initial);
  const [selected, setSelected] = useState(() => {
    const saved = readExamQuestion(userId, initial.id);
    return initial.items.find((item) => item.question_id === saved)?.question_id ?? initial.items[0]?.question_id;
  });
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
  const questionPanel = useRef<HTMLElement>(null);
  const working = exam.status === "working" && remaining > 0 && !closedByServer;
  const finished = exam.status === "submitted";
  const normalized = question && hasAnswer(draft, question.question.question_type) ? draft : null;
  const dirty = Boolean(question && !sameAnswer(normalized, question.response));
  const answerPending = !finished && (dirty || saving);

  function selectQuestion(id: number) {
    rememberExamQuestion(userId, exam.id, id);
    setSelected(id);
  }

  function openOriginal(id: number) {
    selectQuestion(id);
    questionPanel.current?.focus({ preventScroll: true });
    questionPanel.current?.scrollIntoView({ block: "start" });
  }

  useLayoutEffect(() => {
    if (!answerPending) return;
    onAnswerPending(true);
    return () => onAnswerPending(false);
  }, [answerPending, onAnswerPending]);

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
    if (!answerPending) return;
    function warn(event: BeforeUnloadEvent) { event.preventDefault(); }
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [answerPending]);

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
  const missed = exam.items.filter((item) => (item.earned_score ?? 0) < item.max_score).length;
  const minutes = Math.floor(remaining / 60);
  const countdown = String(minutes).padStart(2, "0") + ":" + String(remaining % 60).padStart(2, "0");
  return <div className="written-practice-list">
    <section className="card">
      <div className="card-head"><h2>{exam.title}</h2>
        <button className="ghost" type="button" disabled={submitting} onClick={onBack}>返回试卷</button>
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
          disabled={working && (dirty || saving)} onClick={() => selectQuestion(item.question_id)}>
          {item.number} · {finished ? `${item.earned_score}/${item.max_score} 分` : item.answered ? "已答" : "未答"}
        </button>)}
      </nav>
      {working && saveError && <ErrorNotice message={saveError} onRetry={() => {
        if (conflict) setReload((value) => value + 1);
        else setSaveError("");
      }} />}
    </section>
    {finished && missed > 0 && <>
      <WrittenExamStudy examId={exam.id} onPractice={onStudyPractice} />
      <ExamReview id={exam.id} count={missed} onReviewQuestion={openOriginal} onRetry={onRetry} onAttempt={onAttempt} />
    </>}
    <section ref={questionPanel} tabIndex={-1} className="written-exam-question" aria-label="当前试卷题目">
      {loading && <p role="status">正在读取题面…</p>}
      {loadError && <ErrorNotice message={loadError} onRetry={() => setReload((value) => value + 1)} />}
      {!loading && question?.card.question_id === selected && question && <>
        <ExamQuestion value={question} draft={draft} disabled={!working || submitting || conflict}
          onChange={(response) => { setDraft(response); setSaveError(""); }} />
        {finished && question.answer && question.response && question.card.earned_score !== null
          && question.card.earned_score < question.card.max_score && <WrittenHelp key={question.card.question_id} userId={userId}
            target={{ examId: exam.id, questionId: question.card.question_id }} />}
      </>}
    </section>
    {confirm && working && <Modal title="确认交卷" busy={submitting} error={submitError} onClose={() => { setConfirm(false); setSubmitError(""); }}>
      <p>已答 {answered} 题，未答 {exam.items.length - answered} 题。交卷后可查看成绩和解析。</p>
      <button className="btn-primary" type="button" disabled={submitting} onClick={() => void submit()}>确认交卷</button>
    </Modal>}
  </div>;
}

function ExamReview({ id, count, onRetry, onAttempt, onReviewQuestion }: {
  id: number;
  count: number;
  onRetry: (item: WrittenExamReviewItem) => void;
  onAttempt: (id: number) => void;
  onReviewQuestion: (id: number) => void;
}) {
  const [items, setItems] = useState<WrittenExamReviewItem[]>();
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    if (!count) return;
    const request = new AbortController();
    setError("");
    void api.writtenExamReview(id, request.signal)
      .then((value) => { if (!request.signal.aborted) setItems(value.items); })
      .catch((failure) => { if (!request.signal.aborted) setError(messageOf(failure)); });
    return () => request.abort();
  }, [id, count, reload]);
  if (!count) return null;
  return <details className="card written-task-topics" open>
    <summary>错题复练 · {count} 题</summary>
    <p className="muted">答错和未答的题可以重新练习，复练成绩不改变原卷分。</p>
    {error && <ErrorNotice message={error} onRetry={() => setReload((n) => n + 1)} />}
    {!items && !error && <p role="status">正在读取复练状态…</p>}
    {items && <div className="table-scroll"><table aria-label="模拟错题复练">
      <thead><tr><th scope="col">原卷题号</th><th scope="col">原卷结果</th><th scope="col">复练进度</th><th scope="col">操作</th></tr></thead>
      <tbody>{items.map((item) => <tr key={item.question_id}>
        <th scope="row">第 {item.number} 题</th>
        <td>{item.answered ? "答错" : "未作答"}</td>
        <td>{item.latest_attempt?.correct ? "已复练答对" : item.latest_attempt ? "复练未对" : "待复练"}</td>
        <td><div className="written-practice-actions">
          <button className="ghost" type="button" onClick={() => onReviewQuestion(item.question_id)}>查看原题</button>
          {item.available && <button className="ghost" type="button" onClick={() => onRetry(item)}>
            {item.latest_attempt ? "再练一次" : "开始复练"}
          </button>}
          {item.latest_attempt && <button className="ghost" type="button" onClick={() => onAttempt(item.latest_attempt!.id)}>查看复练记录</button>}
          {!item.available && <span className="muted">原版已下架或更新</span>}
        </div></td>
      </tr>)}</tbody>
    </table></div>}
  </details>;
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
