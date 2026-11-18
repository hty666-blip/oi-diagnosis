import { useEffect, useState } from "react";
import { api, messageOf, type WrittenClassExamQuestions, type WrittenClassExamResults, type WrittenPublishedSummary } from "../api";
import Modal from "./Modal";
import { ErrorNotice } from "./RecordUI";

export interface ExamFollowup {
  classId: number;
  title: string;
  questions: WrittenPublishedSummary[];
}

export default function WrittenExamResults({ onFollowup }: { onFollowup: (draft: ExamFollowup) => void }) {
  const [classes, setClasses] = useState<{ id: number; name: string }[]>();
  const [selected, setSelected] = useState<number>();
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const classId = selected ?? classes?.[0]?.id;

  useEffect(() => {
    const request = new AbortController();
    void api.writtenAssignmentClasses(request.signal).then(
      (value) => { if (!request.signal.aborted) setClasses(value); },
      (failure) => { if (!request.signal.aborted) setError(messageOf(failure)); },
    );
    return () => request.abort();
  }, [reload]);

  return <section className="card" aria-label="模拟学情">
    {error ? <ErrorNotice message={error} onRetry={() => { setError(""); setReload((value) => value + 1); }} />
      : !classes ? <p role="status">正在读取班级…</p>
      : classId === undefined ? <p className="muted">暂无可查看的班级。</p>
      : <>
        <div className="admin-actions written-practice-filters">
          <label>班级<select value={classId} onChange={(event) => setSelected(Number(event.target.value))}>
            {classes.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}
          </select></label>
        </div>
        <ClassResultTable key={classId} classId={classId} onFollowup={onFollowup} />
      </>}
  </section>;
}

function ClassResultTable({ classId, onFollowup }: { classId: number; onFollowup: (draft: ExamFollowup) => void }) {
  const [page, setPage] = useState<WrittenClassExamResults>();
  const [examId, setExamId] = useState<number>();
  const [cursors, setCursors] = useState<number[]>([]);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const beforeId = cursors[cursors.length - 1];

  useEffect(() => {
    const request = new AbortController();
    void api.writtenClassExamResults(classId, beforeId, request.signal).then(
      (value) => { if (!request.signal.aborted) setPage(value); },
      (failure) => { if (!request.signal.aborted) setError(messageOf(failure)); },
    );
    return () => request.abort();
  }, [classId, beforeId, reload]);

  function refresh() {
    setError("");
    setPage(undefined);
    setReload((value) => value + 1);
  }

  function changePage(next: number[]) {
    setPage(undefined);
    setCursors(next);
  }

  if (error) return <ErrorNotice message={error} onRetry={refresh} />;
  if (!page) return <p role="status">正在读取模拟学情…</p>;

  const next = page.next_before_id;
  return <>
    <div className="written-progress-heading">
      <h2>已交答卷</h2>
      <button type="button" className="ghost" onClick={refresh}>刷新学情</button>
    </div>
    {page.items.length === 0 ? <p className="muted">当前没有已交答卷。</p> : <div className="table-scroll">
      <table aria-label="班级模拟答卷">
        <thead><tr><th scope="col">学生</th><th scope="col">试卷</th><th scope="col">交卷时间</th>
          <th scope="col">原卷得分</th><th scope="col">复练答对 / 原卷错题与未答</th><th scope="col">操作</th></tr></thead>
        <tbody>{page.items.map((row) => <tr key={row.id}>
          <th scope="row">{row.student_name}</th><td>{row.title}</td>
          <td><time dateTime={row.submitted_at}>{new Date(row.submitted_at).toLocaleString("zh-CN")}</time></td>
          <td>{row.earned_score} / {row.max_score} 分</td>
          <td>{row.missed_count ? `${row.corrected_count} / ${row.missed_count} 题` : "无错题"}</td>
          <td><button type="button" className="ghost" onClick={() => setExamId(row.id)}
            aria-label={`查看《${row.title}》单题学情`}>查看单题学情</button></td>
        </tr>)}</tbody>
      </table>
    </div>}
    {(beforeId !== undefined || next !== null) && <div className="written-practice-actions">
      <button type="button" className="ghost" disabled={beforeId === undefined} onClick={() => changePage(cursors.slice(0, -1))}>上一页</button>
      <button type="button" className="ghost" disabled={next === null} onClick={() => { if (next !== null) changePage([...cursors, next]); }}>下一页</button>
    </div>}
    {examId !== undefined && <ClassExamQuestions classId={classId} examId={examId} onClose={() => setExamId(undefined)}
      onFollowup={(draft) => { setExamId(undefined); onFollowup(draft); }} />}
  </>;
}

function ClassExamQuestions({ classId, examId, onClose, onFollowup }: {
  classId: number;
  examId: number;
  onClose: () => void;
  onFollowup: (draft: ExamFollowup) => void;
}) {
  const [report, setReport] = useState<WrittenClassExamQuestions>();
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const request = new AbortController();
    void api.writtenClassExamQuestions(classId, examId, request.signal).then(
      (value) => { if (!request.signal.aborted) setReport(value); },
      (failure) => { if (!request.signal.aborted) setError(messageOf(failure)); },
    );
    return () => request.abort();
  }, [classId, examId, reload]);

  const chosen = report?.items.flatMap((row) => row.practice_question && selected.includes(row.version_id) ? [row.practice_question] : []) ?? [];
  return <Modal title="模拟卷单题学情" onClose={onClose} wide>
    {error ? <ErrorNotice message={error} onRetry={() => { setError(""); setReload((value) => value + 1); }} />
      : !report ? <p role="status">正在读取单题学情…</p>
      : <>
        <div className="card-head"><h3>{report.title}</h3><span className="muted">已交 {report.submitted_count} 份</span></div>
        <p className="muted">同一题的不同版本分行统计。</p>
        {report.items.length ? <div className="table-scroll"><table aria-label="模拟卷逐题统计">
          <thead><tr><th scope="col">答卷题号</th><th scope="col">版本</th><th scope="col">本版卷数</th><th scope="col">卷内答对</th>
            <th scope="col">卷内答错</th><th scope="col">未答</th><th scope="col">复练答对</th><th scope="col">补练选题</th></tr></thead>
          <tbody>{report.items.map((row) => <tr key={`${row.question_id}:${row.version_id}:${row.number}`}>
            <th scope="row">第 {row.number} 题</th><td>v{row.revision}</td><td>{row.answer_count}</td>
            <td>{row.correct_count}</td><td>{row.wrong_count}</td><td>{row.blank_count}</td><td>{row.corrected_count}</td>
            <td>{row.practice_question ? <input type="checkbox" aria-label={`选入第 ${row.number} 题 v${row.revision}`}
              checked={selected.includes(row.version_id)} disabled={chosen.length >= 10 && !selected.includes(row.version_id)}
              onChange={(event) => {
                const checked = event.currentTarget.checked;
                setSelected((values) => checked ? [...values, row.version_id] : values.filter((id) => id !== row.version_id));
              }} />
              : <span className="muted">{row.wrong_count + row.blank_count > row.corrected_count ? "原版不可用" : "无需补练"}</span>}</td>
          </tr>)}</tbody>
        </table></div> : <p className="muted">暂无可统计的作答。</p>}
        {report.items.some((row) => row.practice_question) && <div className="admin-actions">
          <button className="btn-primary" type="button" disabled={chosen.length === 0}
            onClick={() => onFollowup({ classId, title: "模拟错题补练", questions: chosen })}>用所选题创建专项</button>
          <span className="muted">已选 {chosen.length} / 10</span>
        </div>}
      </>}
  </Modal>;
}
