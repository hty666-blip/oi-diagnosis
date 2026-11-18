import { useEffect, useState } from "react";
import { api, messageOf, type WrittenClassExamResults } from "../api";
import { ErrorNotice } from "./RecordUI";

export default function WrittenExamResults() {
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
        <ClassResultTable key={classId} classId={classId} />
      </>}
  </section>;
}

function ClassResultTable({ classId }: { classId: number }) {
  const [page, setPage] = useState<WrittenClassExamResults>();
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
          <th scope="col">原卷得分</th><th scope="col">复练答对 / 原卷错题与未答</th></tr></thead>
        <tbody>{page.items.map((row) => <tr key={row.id}>
          <th scope="row">{row.student_name}</th><td>{row.title}</td>
          <td><time dateTime={row.submitted_at}>{new Date(row.submitted_at).toLocaleString("zh-CN")}</time></td>
          <td>{row.earned_score} / {row.max_score} 分</td>
          <td>{row.missed_count ? `${row.corrected_count} / ${row.missed_count} 题` : "无错题"}</td>
        </tr>)}</tbody>
      </table>
    </div>}
    {(beforeId !== undefined || next !== null) && <div className="written-practice-actions">
      <button type="button" className="ghost" disabled={beforeId === undefined} onClick={() => changePage(cursors.slice(0, -1))}>上一页</button>
      <button type="button" className="ghost" disabled={next === null} onClick={() => { if (next !== null) changePage([...cursors, next]); }}>下一页</button>
    </div>}
  </>;
}
