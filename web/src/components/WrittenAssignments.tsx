import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, messageOf, type WrittenAssignmentDetail, type WrittenAssignmentSummary, type WrittenAttempt, type WrittenPublishedSummary } from "../api";
import Modal from "./Modal";
import { ErrorNotice } from "./RecordUI";
import WrittenExamResults, { type ExamFollowup } from "./WrittenExamResults";
import WrittenShortReview from "./WrittenShortReview";
import { WrittenOpenAttempt, WrittenOpenQuestion, WrittenScoreBadge, writtenTypeLabel } from "./WrittenPracticeQuestion";

type TaskQuestion = WrittenAssignmentDetail["questions"][number];
type OpenTask = { kind: "question"; question: TaskQuestion } | { kind: "attempt"; question: TaskQuestion; id: number };

const deadlineText = (value: string) => new Date(value).toLocaleString("zh-CN");

export default function WrittenAssignments({ studentId, assignmentId, onSelected }: {
  studentId?: number; assignmentId?: number; onSelected?: (id?: number) => void;
}) {
  const student = studentId !== undefined;
  const [showExams, setShowExams] = useState(false);
  const [tasks, setTasks] = useState<WrittenAssignmentSummary[]>();
  const [localSelected, setSelected] = useState<number>();
  const [loadedTask, setDetail] = useState<WrittenAssignmentDetail>();
  const [target, setTarget] = useState<OpenTask>();
  const [receipt, setReceipt] = useState<WrittenAttempt>();
  const [publishing, setPublishing] = useState(false);
  const [followup, setFollowup] = useState<ExamFollowup>();
  const [reviewing, setReviewing] = useState<number>();
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const refresh = () => setReload((value) => value + 1);
  const selected = onSelected ? assignmentId : localSelected;
  const detail = loadedTask?.id === selected ? loadedTask : undefined;

  useEffect(() => {
    if (showExams) return;
    const request = new AbortController();
    setError("");
    const loading = selected === undefined
      ? api.writtenAssignments(request.signal).then((value) => { if (!request.signal.aborted) setTasks(value); })
      : api.writtenAssignment(selected, request.signal).then((value) => { if (!request.signal.aborted) setDetail(value); });
    void loading.catch((failure) => { if (!request.signal.aborted) setError(messageOf(failure)); });
    return () => request.abort();
  }, [showExams, selected, reload]);

  function open(id?: number) {
    setDetail(undefined);
    setTarget(undefined);
    if (onSelected) onSelected(id);
    else setSelected(id);
  }

  function start(question: TaskQuestion) {
    setReceipt(undefined);
    setTarget({ kind: "question", question });
  }

  function backToTask() {
    setTarget(undefined);
    setReceipt(undefined);
    refresh();
  }

  function beginPublish(draft?: ExamFollowup) {
    setFollowup(draft);
    setPublishing(true);
  }

  function closePublish() {
    setFollowup(undefined);
    setPublishing(false);
  }

  const publishDialog = publishing && <PublishTask seed={followup} onClose={closePublish} onPublished={(id) => {
    closePublish();
    setShowExams(false);
    open(id);
  }} />;
  const teacherNavigation = !student && <>
    <header className="page-head">
      <h1>初赛任务</h1>
      {!showExams && <button type="button" className="btn-primary" onClick={() => beginPublish()}>发布专项</button>}
    </header>
    <nav className="admin-tabs" aria-label="初赛任务">
      <button type="button" className="ghost" aria-pressed={!showExams} onClick={() => setShowExams(false)}>班级专项</button>
      <button type="button" className="ghost" aria-pressed={showExams} onClick={() => setShowExams(true)}>模拟学情</button>
    </nav>
  </>;

  if (showExams) return <section>{teacherNavigation}<WrittenExamResults onFollowup={beginPublish} />{publishDialog}</section>;

  return <section>
    {teacherNavigation}
    {selected !== undefined && <div className="written-task-nav">
      <button type="button" className="ghost" onClick={target ? backToTask : () => open()}>
        {target ? "返回专项" : "返回任务列表"}
      </button>
      {target && <span className="muted">{detail?.title}</span>}
      {!target && <button type="button" className="ghost" onClick={refresh}>刷新进度</button>}
    </div>}
    {error && <ErrorNotice message={error} onRetry={refresh} />}
    {!error && selected === undefined && <section className="card">
      {!tasks ? <p role="status">正在读取初赛任务…</p>
        : !tasks.length ? <p>{student ? "暂未收到初赛任务。" : "暂无初赛任务。发布专项后可以在这里查看班级进度。"}</p>
        : <div className="table-scroll"><table>
          <thead><tr><th>专项名称</th><th>班级</th><th>截止时间</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>{tasks.map((task) => <tr key={task.id}>
            <td>{task.title}</td><td>{task.class_name}</td><td>{deadlineText(task.due_at)}</td>
            <td><span className={`badge ${task.is_closed ? "" : "route"}`}>{task.is_closed ? "已截止" : "进行中"}</span></td>
            <td><button type="button" className="ghost" onClick={() => open(task.id)}>{student ? "打开任务" : "查看学情"}</button></td>
          </tr>)}</tbody>
        </table></div>}
    </section>}
    {!error && selected !== undefined && !detail && <p role="status" className="card">正在读取专项…</p>}
    {detail && !target && !error && <section className="card written-task-detail">
      <div className="card-head">
        <h2>{detail.title}</h2>
        <p className="muted">{detail.class_name} · 截止 {deadlineText(detail.due_at)}</p>
      </div>
      {student ? <TaskProblems task={detail} onAnswer={start} onReview={(id, question) => {
        setReceipt(undefined);
        setTarget({ kind: "attempt", id, question });
      }} /> : <TaskMatrix task={detail} onReview={setReviewing} />}
    </section>}
    {detail && studentId !== undefined && target?.kind === "question" && <WrittenOpenQuestion
      key={target.question.id} userId={studentId} target={{
        id: target.question.id, version: target.question.version_id, assignmentId: detail.id, closed: detail.is_closed,
      }} onVersion={() => {}} onSubmitted={(value) => {
        setReceipt(value);
        setTarget({ kind: "attempt", id: value.id, question: target.question });
      }} />}
    {detail && studentId !== undefined && target?.kind === "attempt" && <WrittenOpenAttempt
      key={target.id} userId={studentId} id={target.id} immediate={receipt} taskContext={detail.id}
      onRetryQuestion={detail.is_closed || !target.question.is_available ? undefined : () => start(target.question)} />}
    {publishDialog}
    {detail && reviewing !== undefined && <WrittenShortReview key={reviewing} assignmentId={detail.id} attemptId={reviewing}
      onClose={() => setReviewing(undefined)} onConfirmed={() => { setReviewing(undefined); refresh(); }} />}
  </section>;
}

function TaskProblems({ task, onAnswer, onReview }: {
  task: WrittenAssignmentDetail;
  onAnswer: (question: TaskQuestion) => void;
  onReview: (id: number, question: TaskQuestion) => void;
}) {
  return <div className="table-scroll"><table>
    <thead><tr><th>题目</th><th>题型</th><th>最新成绩</th><th>操作</th></tr></thead>
    <tbody>{task.questions.map((question, index) => {
      const cell = task.rows[0]?.cells[index];
      return <tr key={question.id}>
        <td><div className="written-excerpt">{question.stem}</div><small className="muted">CSP-{question.level} · v{question.revision}</small></td>
        <td>{writtenTypeLabel[question.question_type]}</td>
        <td>{cell ? question.question_type === "short_answer" && cell.earned_score !== null
          ? <span className="badge route">已复核 · {cell.earned_score} / {question.score} 分</span>
          : <WrittenScoreBadge correct={cell.correct} /> : <span className="muted">未交卷</span>}</td>
        <td><div className="written-practice-actions">
          {!task.is_closed && question.is_available && (!cell || question.question_type !== "short_answer") && <button type="button" className="ghost" onClick={() => onAnswer(question)}>{cell ? "再练一次" : "开始作答"}</button>}
          {cell && <button type="button" className="ghost" onClick={() => onReview(cell.id, question)}>查看作答</button>}
          {!question.is_available && <span className="muted">已下架</span>}
          {task.is_closed && !cell && question.is_available && <span className="muted">已截止</span>}
        </div></td>
      </tr>;
    })}</tbody>
  </table></div>;
}

function TaskMatrix({ task, onReview }: { task: WrittenAssignmentDetail; onReview: (id: number) => void }) {
  const [filter, setFilter] = useState<{ index: number; kind: "wrong" | "missing" }>();
  const visible = filter ? task.rows.filter((row) => {
    const cell = row.cells[filter.index];
    return filter.kind === "missing" ? cell == null : cell?.correct === false;
  }) : task.rows;
  return <>
    <p className="written-task-summary">
      <span>全部交卷 <strong>{task.summary.submitted_all} / {task.summary.students}</strong></span>
      <span>全部答对 <strong>{task.summary.correct_all} / {task.summary.students}</strong></span>
    </p>
    <details className="written-task-topics"><summary>单题学情</summary>
      <div className="table-scroll"><table>
        <thead><tr><th scope="col">题目</th><th scope="col">满分</th><th scope="col">未满分</th><th scope="col">待复核</th><th scope="col">未交</th></tr></thead>
        <tbody>{task.questions.map((question, index) => {
          const cells = task.rows.map((row) => row.cells[index]);
          const right = cells.filter((cell) => cell?.correct === true).length;
          const wrong = cells.filter((cell) => cell?.correct === false).length;
          const pending = cells.filter((cell) => cell?.correct === null).length;
          const missing = cells.length - right - wrong - pending;
          return <tr key={question.id}>
            <th scope="row"><div className="written-excerpt">第 {index + 1} 题 · {question.stem}</div>
              <small className="muted">v{question.revision}{question.is_available ? "" : " · 已下架"}</small></th>
            <td>{right}</td>
            <td>{wrong > 0 ? <button className="ghost" type="button" aria-label={`查看第 ${index + 1} 题未满分的 ${wrong} 人`}
              onClick={() => setFilter({ index, kind: "wrong" })}>{wrong} 人</button> : "0"}</td>
            <td>{pending}</td>
            <td>{missing > 0 ? <button className="ghost" type="button" aria-label={`查看第 ${index + 1} 题未交的 ${missing} 人`}
              onClick={() => setFilter({ index, kind: "missing" })}>{missing} 人</button> : "0"}</td>
          </tr>;
        })}</tbody>
      </table></div>
    </details>
    {filter && <div className="written-task-nav">
      <span role="status">第 {filter.index + 1} 题{filter.kind === "wrong" ? "未满分" : "未交"} · {visible.length} 人</span>
      <button type="button" className="ghost" onClick={() => setFilter(undefined)}>恢复全班名单</button>
    </div>}
    {visible.length === 0 ? <p className="muted">{filter ? "当前没有符合条件的学生。" : "当前班级无名单内学生。"}</p> : <div className="table-scroll"><table aria-label="班级逐题学情">
      <thead><tr><th scope="col">学生</th>
        {task.questions.map((question, index) => <th key={question.id} scope="col">第 {index + 1} 题</th>)}
        <th scope="col">已交</th><th scope="col">答对</th>
      </tr></thead>
      <tbody>{visible.map((row) => <tr key={row.student_id}>
        <th scope="row">{row.name}</th>
        {row.cells.map((cell, index) => <td key={task.questions[index].id}>
          {cell && task.questions[index].question_type === "short_answer"
            ? <button type="button" className="ghost" onClick={() => onReview(cell.id)}>
              {cell.earned_score === null ? "待复核" : `${cell.earned_score} / ${task.questions[index].score} 分`}
            </button> : cell ? <WrittenScoreBadge correct={cell.correct} /> : <span className="muted">未交卷</span>}
        </td>)}
        <td>{row.submitted_count} / {task.questions.length}</td><td>{row.correct_count} / {task.questions.length}</td>
      </tr>)}</tbody>
    </table></div>}
  </>;
}

function PublishTask({ seed, onClose, onPublished }: { seed?: ExamFollowup; onClose: () => void; onPublished: (id: number) => void }) {
  const [groups, setGroups] = useState<{ id: number; name: string }[]>();
  const [classId, setClassId] = useState(seed ? String(seed.classId) : "");
  const [studentIds, setStudentIds] = useState<number[] | null>(seed?.studentIds ?? null);
  const [audienceReady, setAudienceReady] = useState(false);
  const [title, setTitle] = useState(seed?.title ?? "");
  const [deadline, setDeadline] = useState(() => {
    const date = new Date();
    date.setDate(date.getDate() + 7);
    return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
  });
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [catalog, setCatalog] = useState<{ items: WrittenPublishedSummary[]; has_more: boolean }>();
  const [picks, setPicks] = useState<WrittenPublishedSummary[]>(seed?.questions ?? []);
  const [error, setError] = useState("");
  const [catalogError, setCatalogError] = useState("");
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const lastRequest = useRef<{ draft: string; id: string }>();
  const write = useRef<AbortController>();
  const validClass = groups?.some((group) => String(group.id) === classId);
  const validAudience = studentIds === null || (audienceReady && studentIds.length > 0);

  useEffect(() => () => write.current?.abort(), []);
  useEffect(() => {
    const request = new AbortController();
    void api.writtenAssignmentClasses(request.signal).then(
      (value) => { if (!request.signal.aborted) setGroups(value); },
      (failure) => { if (!request.signal.aborted) setError(messageOf(failure)); },
    );
    return () => request.abort();
  }, []);
  useEffect(() => {
    const request = new AbortController();
    setCatalog(undefined);
    setCatalogError("");
    void api.writtenCatalog(offset, { q: query, level: "", question_type: "" }, request.signal).then(
      (value) => { if (!request.signal.aborted) setCatalog(value); },
      (failure) => { if (!request.signal.aborted) setCatalogError(messageOf(failure)); },
    );
    return () => request.abort();
  }, [offset, query, reload]);

  async function publish(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (write.current || !validClass || !validAudience || !picks.length || !deadline || !title.trim()) return;
    const date = new Date(deadline);
    if (!Number.isFinite(date.getTime()) || date.getTime() <= Date.now()) { setError("截止时间必须晚于当前时间。"); return; }
    const body = {
      title: title.trim(), class_id: Number(classId), due_at: date.toISOString(),
      questions: picks.map(({ id, version_id }) => ({ id, version_id })),
      ...(studentIds !== null ? { student_ids: [...studentIds].sort((left, right) => left - right) } : {}),
    };
    const draft = JSON.stringify(body);
    if (lastRequest.current?.draft !== draft) lastRequest.current = { draft, id: crypto.randomUUID() };
    const request = new AbortController();
    write.current = request;
    setBusy(true);
    setError("");
    try {
      const task = await api.publishWrittenAssignment({ ...body, request_id: lastRequest.current.id }, request.signal);
      if (!request.signal.aborted) onPublished(task.id);
    } catch (failure) {
      if (!request.signal.aborted) setError(messageOf(failure));
    } finally {
      if (!request.signal.aborted) { write.current = undefined; setBusy(false); }
    }
  }

  return <Modal title="发布初赛专项" wide busy={busy} error={error} onClose={onClose}>
    <form className="written-task-editor" onSubmit={(event) => void publish(event)}>
      <fieldset disabled={busy}>
        <label>专项名称<input required maxLength={120} value={title} onChange={(event) => setTitle(event.target.value)} /></label>
        <label>班级<select required disabled={seed !== undefined} value={classId} onChange={(event) => {
          setClassId(event.target.value);
          setAudienceReady(false);
          if (studentIds !== null) setStudentIds([]);
        }}>
          <option value="">{groups ? "选择班级" : "正在读取班级…"}</option>
          {groups?.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}
        </select></label>
        <label>截止时间<input type="datetime-local" required value={deadline} onChange={(event) => setDeadline(event.target.value)} /></label>
      </fieldset>
      {groups && seed && !validClass && <p className="muted">原班级已不可布置专项，请返回模拟学情。</p>}
      {!seed && groups?.length === 0 && <p className="muted">当前没有可布置任务的班级。</p>}
      <TaskAudience key={classId} classId={validClass ? Number(classId) : undefined} studentIds={studentIds}
        busy={busy} onChange={setStudentIds} onReady={setAudienceReady} />
      <fieldset disabled={busy}>
        <legend>选题</legend>
        <div className="written-practice-actions">
          <input type="search" aria-label="按题干或编号查题" maxLength={120} value={search} onChange={(event) => setSearch(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); setOffset(0); setQuery(search.trim()); } }} />
          <button className="ghost" type="button" onClick={() => { setOffset(0); setQuery(search.trim()); }}>查询</button>
          <span className="muted">已选 {picks.length} / 10</span>
        </div>
        {picks.length > 0 && <ol className="written-task-picked">{picks.map((question) => <li key={question.id}>
          <span className="written-excerpt">{question.stem}</span>
          <button className="ghost" type="button" onClick={() => setPicks((value) => value.filter((item) => item.id !== question.id))}
            aria-label={`移除题目 #${question.id}`}>移除</button>
        </li>)}</ol>}
        {catalogError ? <ErrorNotice message={catalogError} onRetry={() => setReload((value) => value + 1)} />
          : !catalog ? <p role="status">正在读取题库…</p>
          : !catalog.items.length ? <p className="muted">{query ? "没有符合查询条件的题目。" : "题库暂无已发布题目。"}</p>
          : <div className="table-scroll written-task-catalog"><table>
            <thead><tr><th>已审核题目</th><th>级别</th><th>操作</th></tr></thead>
            <tbody>{catalog.items.map((question) => {
              const chosen = picks.some((pick) => pick.id === question.id);
              return <tr key={question.id}><td><div className="written-excerpt">{question.stem}</div></td>
                <td>CSP-{question.level}</td><td><button className="ghost" type="button" disabled={chosen || picks.length >= 10}
                  onClick={() => setPicks((value) => [...value, question])}>{chosen ? "已选" : "选入"}</button></td></tr>;
            })}</tbody>
          </table></div>}
        {(offset > 0 || catalog?.has_more) && <div className="written-practice-actions">
          <button className="ghost" type="button" disabled={offset === 0} onClick={() => setOffset(offset - 25)}>上一页</button>
          <button className="ghost" type="button" disabled={!catalog?.has_more} onClick={() => setOffset(offset + 25)}>下一页</button>
        </div>}
      </fieldset>
      <div className="written-practice-actions">
        <button className="btn-primary" type="submit" disabled={busy || !validClass || !validAudience || !title.trim() || !deadline || !picks.length}>
          {studentIds === null ? "发布给全班" : "发布给所选学生"}
        </button>
        <button className="ghost" type="button" disabled={busy} onClick={onClose}>取消</button>
      </div>
    </form>
  </Modal>;
}

function TaskAudience({ classId, studentIds, busy, onChange, onReady }: {
  classId?: number;
  studentIds: number[] | null;
  busy: boolean;
  onChange: (value: number[] | null) => void;
  onReady: (ready: boolean) => void;
}) {
  const [students, setStudents] = useState<{ id: number; name: string; username: string }[]>();
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const targeted = studentIds !== null;
  const rosterChanged = students && targeted && studentIds.some((id) => !students.some((student) => student.id === id));

  useEffect(() => {
    if (classId === undefined || !targeted) return;
    const request = new AbortController();
    void api.writtenAssignmentStudents(classId, request.signal).then(
      (rows) => { if (!request.signal.aborted) setStudents(rows); },
      (failure) => { if (!request.signal.aborted) setError(messageOf(failure)); },
    );
    return () => request.abort();
  }, [classId, targeted, reload]);
  useEffect(() => { onReady(students !== undefined && !rosterChanged); }, [students, rosterChanged, onReady]);

  function refresh() {
    setStudents(undefined);
    setError("");
    onChange([]);
    setReload((value) => value + 1);
  }

  return <fieldset disabled={busy || classId === undefined}>
    <legend>发布范围</legend>
    <div className="training-audience">
      <label><input name="written-audience" type="radio" checked={!targeted} onChange={() => onChange(null)} />全班</label>
      <label><input name="written-audience" type="radio" checked={targeted} onChange={refresh} />指定学生</label>
    </div>
    {targeted && classId !== undefined && (error ? <ErrorNotice message={error} onRetry={refresh} />
      : !students ? <p role="status">正在读取班级名单…</p>
      : !students.length ? <p className="muted">当前班级暂无启用的学生。</p>
      : <>
        {rosterChanged && <p role="status">所选学生已不在当前名单，请刷新名单重新选择。</p>}
        <div className="training-audience written-task-student-list" role="group" aria-label="指定学生名单">
          {students.map((student) => {
            const chosen = studentIds.includes(student.id);
            return <label key={student.id}>
              <input type="checkbox" checked={chosen} disabled={!chosen && studentIds.length >= 200} onChange={(event) => onChange(
                event.currentTarget.checked ? [...studentIds, student.id] : studentIds.filter((id) => id !== student.id),
              )} />
              {student.name === student.username ? student.name : `${student.name}（${student.username}）`}
            </label>;
          })}
        </div>
        <div className="written-practice-actions">
          <span className="muted">已选 {studentIds.length} 人</span>
          <button type="button" className="ghost" onClick={refresh}>刷新名单</button>
        </div>
      </>)}
  </fieldset>;
}
