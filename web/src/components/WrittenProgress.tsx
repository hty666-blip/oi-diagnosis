import { useEffect, useState } from "react";
import { api, messageOf, type WrittenProgressRow } from "../api";
import type { WrittenTarget } from "../written-practice";
import { ErrorNotice } from "./RecordUI";

const statusLabel = {
  practiced: "有练习记录",
  needs_review: "待巩固",
  reviewed: "新题答对",
};
const statusTone = { practiced: "", needs_review: "warn", reviewed: "ok" };

export function WrittenProgress({ onBank, onOpen }: {
  onBank: () => void;
  onOpen: (target: WrittenTarget) => void;
}) {
  const [page, setPage] = useState<{ items: WrittenProgressRow[]; attempt_limit: number }>();
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const request = new AbortController();
    setError("");
    void api.writtenProgress(request.signal).then(
      (value) => { if (!request.signal.aborted) setPage(value); },
      (failure) => { if (!request.signal.aborted) setError(messageOf(failure)); },
    );
    return () => request.abort();
  }, [reload]);

  return <section className="card written-progress" aria-label="学习路线">
    {error ? <ErrorNotice message={error} onRetry={() => setReload((value) => value + 1)} />
      : !page ? <p role="status">正在读取学习路线…</p>
      : page.items.length === 0 ? <div className="written-empty">
        <p>完成第一道初赛题后，这里会显示对应知识点。</p>
        <button type="button" className="btn-primary" onClick={onBank}>前往题库</button>
      </div> : <>
        <div className="written-progress-heading">
          <h2>知识点练习</h2>
          <span className="muted">最多统计最近 {page.attempt_limit} 次作答</span>
        </div>
        <div className="table-scroll"><table>
          <thead><tr><th scope="col">知识点</th><th scope="col">级别</th><th scope="col">作答次数</th><th scope="col">进度</th><th scope="col">下一步</th></tr></thead>
          <tbody>{page.items.map((row) => {
            const next = row.next_question;
            const recordId = row.review_attempt_id ?? row.last_error_id ?? row.latest_attempt_id;
            return <tr key={`${row.batch_id}:${row.level}:${row.point.id}`}>
              <th scope="row">
                <div>{row.point.name}</div>
                {row.point.prerequisites.length > 0 && <small className="muted">先学 {row.point.prerequisites.join("、")}</small>}
              </th>
              <td>CSP-{row.level}</td>
              <td>{row.attempt_count}</td>
              <td><span className={`badge ${statusTone[row.status]}`}>{statusLabel[row.status]}</span></td>
              <td><div className="written-practice-actions">
                {next && <button type="button" className="btn-primary" onClick={() => onOpen({ kind: "question", id: next.id, version: next.version_id })}>做一道新题</button>}
                <button type="button" className="ghost" onClick={() => onOpen({ kind: "attempt", id: recordId })}>
                  {row.status === "needs_review" ? "查看错题" : "查看作答"}
                </button>
                {row.status === "needs_review" && !next && <span className="muted">暂无新题推荐</span>}
              </div></td>
            </tr>;
          })}</tbody>
        </table></div>
      </>}
  </section>;
}
