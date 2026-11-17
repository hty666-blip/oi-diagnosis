import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, messageOf, type WrittenAssignmentDetail, type WrittenAssignmentSummary, type WrittenAttempt, type WrittenPublishedSummary } from "../api";
import Modal from "./Modal";
import { ErrorNotice } from "./RecordUI";
import { WrittenOpenAttempt, WrittenOpenQuestion, WrittenScoreBadge, writtenTypeLabel } from "./WrittenPracticeQuestion";

type TaskQuestion = WrittenAssignmentDetail["questions"][number];
type OpenTask = { kind: "question"; question: TaskQuestion } | { kind: "attempt"; question: TaskQuestion; id: number };

const deadlineText = (value: string) => new Date(value).toLocaleString("zh-CN");

export default function WrittenAssignments({ studentId }: { studentId?: number }) {
  const student = studentId !== undefined;
  const [tasks, setTasks] = useState<WrittenAssignmentSummary[]>();
  const [selected, setSelected] = useState<number>();
  const [detail, setDetail] = useState<WrittenAssignmentDetail>();
  const [target, setTarget] = useState<OpenTask>();
  const [receipt, setReceipt] = useState<WrittenAttempt>();
  const [publishing, setPublishing] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const refresh = () => setReload((value) => value + 1);

  useEffect(() => {
    const request = new AbortController();
    setError("");
    const loading = selected === undefined
      ? api.writtenAssignments(request.signal).then((value) => { if (!request.signal.aborted) setTasks(value); })
      : api.writtenAssignment(selected, request.signal).then((value) => { if (!request.signal.aborted) setDetail(value); });
    void loading.catch((failure) => { if (!request.signal.aborted) setError(messageOf(failure)); });
    return () => request.abort();
  }, [selected, reload]);

  function open(id?: number) {
    setDetail(undefined);
    setTarget(undefined);
    setSelected(id);
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

  return <section>
    {!student && <header className="page-head">
      <h1>初赛任务</h1>
      <button type="button" className="btn-primary" onClick={() => setPublishing(true)}>发布专项</button>
    </header>}
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
      }} /> : <TaskMatrix task={detail} />}
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
    {publishing && <PublishTask onClose={() => setPublishing(false)} onPublished={(id) => {
      setPublishing(false);
      open(id);
    }} />}
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
        <td>{cell ? <WrittenScoreBadge correct={cell.correct} /> : <span className="muted">未交卷</span>}</td>
        <td><div className="written-practice-actions">
          {!task.is_closed && question.is_available && <button type="button" className="ghost" onClick={() => onAnswer(question)}>{cell ? "再练一次" : "开始作答"}</button>}
          {cell && <button type="button" className="ghost" onClick={() => onReview(cell.id, question)}>查看作答</button>}
          {!question.is_available && <span className="muted">已下架</span>}
          {task.is_closed && !cell && question.is_available && <span className="muted">已截止</span>}
        </div></td>
      </tr>;
    })}</tbody>
  </table></div>;
}

function TaskMatrix({ task }: { task: WrittenAssignmentDetail }) {
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
        <thead><tr><th scope="col">题目</th><th scope="col">答对</th><th scope="col">答错</th><th scope="col">未交</th></tr></thead>
        <tbody>{task.questions.map((question, index) => {
          const cells = task.rows.map((row) => row.cells[index]);
          const right = cells.filter((cell) => cell?.correct === true).length;
          const wrong = cells.filter((cell) => cell?.correct === false).length;
          const missing = cells.length - right - wrong;
          return <tr key={question.id}>
            <th scope="row"><div className="written-excerpt">第 {index + 1} 题 · {question.stem}</div>
              <small className="muted">v{question.revision}{question.is_available ? "" : " · 已下架"}</small></th>
            <td>{right}</td>
            <td>{wrong > 0 ? <button className="ghost" type="button" aria-label={`查看第 ${index + 1} 题答错的 ${wrong} 人`}
              onClick={() => setFilter({ index, kind: "wrong" })}>{wrong} 人</button> : "0"}</td>
            <td>{missing > 0 ? <button className="ghost" type="button" aria-label={`查看第 ${index + 1} 题未交的 ${missing} 人`}
              onClick={() => setFilter({ index, kind: "missing" })}>{missing} 人</button> : "0"}</td>
          </tr>;
        })}</tbody>
      </table></div>
    </details>
    {filter && <div className="written-task-nav">
      <span role="status">第 {filter.index + 1} 题{filter.kind === "wrong" ? "答错" : "未交"} · {visible.length} 人</span>
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
          {cell ? <WrittenScoreBadge correct={cell.correct} /> : <span className="muted">未交卷</span>}
        </td>)}
        <td>{row.submitted_count} / {task.questions.length}</td><td>{row.correct_count} / {task.questions.length}</td>
      </tr>)}</tbody>
    </table></div>}
  </>;
}

function PublishTask({ onClose, onPublished }: { onClose: () => void; onPublished: (id: number) => void }) {
  const [groups, setGroups] = useState<{ id: number; name: string }[]>();
  const [classId, setClassId] = useState("");
  const [title, setTitle] = useState("");
  const [deadline, setDeadline] = useState(() => {
    const date = new Date();
    date.setDate(date.getDate() + 7);
    return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
  });
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [catalog, setCatalog] = useState<{ items: WrittenPublishedSummary[]; has_more: boolean }>();
  const [picks, setPicks] = useState<WrittenPublishedSummary[]>([]);
  const [error, setError] = useState("");
  const [catalogError, setCatalogError] = useState("");
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const lastRequest = useRef<{ draft: string; id: string }>();
  const write = useRef<AbortController>();

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
    if (write.current || !classId || !picks.length || !deadline || !title.trim()) return;
    const date = new Date(deadline);
    if (!Number.isFinite(date.getTime()) || date.getTime() <= Date.now()) { setError("截止时间必须晚于当前时间。"); return; }
    const body = {
      title: title.trim(), class_id: Number(classId), due_at: date.toISOString(),
      questions: picks.map(({ id, version_id }) => ({ id, version_id })),
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
        <label>班级<select required value={classId} onChange={(event) => setClassId(event.target.value)}>
          <option value="">{groups ? "选择班级" : "正在读取班级…"}</option>
          {groups?.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}
        </select></label>
        <label>截止时间<input type="datetime-local" required value={deadline} onChange={(event) => setDeadline(event.target.value)} /></label>
      </fieldset>
      {groups?.length === 0 && <p className="muted">当前没有可布置任务的班级。</p>}
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
        <button className="btn-primary" type="submit" disabled={busy || !classId || !title.trim() || !deadline || !picks.length}>发布给全班</button>
        <button className="ghost" type="button" disabled={busy} onClick={onClose}>取消</button>
      </div>
    </form>
  </Modal>;
}
