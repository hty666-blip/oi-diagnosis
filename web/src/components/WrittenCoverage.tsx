import { useEffect, useState } from "react";
import { api, messageOf, type WrittenCoverageRow } from "../api";
import Modal from "./Modal";
import { ErrorNotice } from "./RecordUI";

export type WrittenCoveragePoint = Pick<WrittenCoverageRow, "id" | "name">;

export default function WrittenCoverage({ batchId, onClose, onPick }: {
  batchId: string;
  onClose: () => void;
  onPick: (point: WrittenCoveragePoint) => void;
}) {
  const [data, setData] = useState<{ items: WrittenCoverageRow[]; unmapped_pending: number }>();
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [missing, setMissing] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const request = new AbortController();
    setError("");
    void api.writtenCoverage(batchId, request.signal).then(
      (value) => { if (!request.signal.aborted) setData(value); },
      (failure) => { if (!request.signal.aborted) setError(messageOf(failure)); },
    );
    return () => request.abort();
  }, [batchId, retry]);
  const q = search.trim().toLowerCase();
  const rows = data?.items.filter((row) => {
    if (missing === "practice" && row.published_j + row.published_s > 0) return false;
    if (missing === "article" && row.article_revision !== null) return false;
    return !q || row.id.toLowerCase().includes(q) || row.name.toLowerCase().includes(q);
  }) ?? [];
  return <Modal title="知识点覆盖" wide onClose={onClose}>
    {error ? <ErrorNotice message={error} onRetry={() => setRetry((value) => value + 1)} />
      : !data ? <p role="status">正在计算覆盖…</p>
      : <>
        <div className="admin-actions written-coverage-controls">
          <label>知识点<input type="search" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
          <label>覆盖状态<select value={missing} onChange={(event) => setMissing(event.target.value)}>
            <option value="">全部知识点</option><option value="practice">无上架题</option><option value="article">无已发布讲义</option>
          </select></label>
        </div>
        <p className="muted">待审按来源标注的主知识点计。上架数按已审核版本的主知识点计。</p>
        {data.unmapped_pending > 0 && <p className="muted">{data.unmapped_pending} 道待审题的原始主知识点未落到叶子节点。</p>}
        <div className="table-scroll"><table>
          <thead><tr>
            <th scope="col">知识点</th><th scope="col">J 待审 / 上架</th><th scope="col">S 待审 / 上架</th>
            <th scope="col">未分级待审</th><th scope="col">讲义</th><th scope="col">操作</th>
          </tr></thead>
          <tbody>{rows.map((row) => <tr key={row.id}>
            <th className="written-coverage-point" scope="row">{row.name}<small className="muted">{row.id}</small></th>
            <td>{row.pending_j} / {row.published_j}</td><td>{row.pending_s} / {row.published_s}</td>
            <td>{row.pending_unclassified}</td><td>{row.article_revision === null ? "未发布" : `v${row.article_revision}`}</td>
            <td>{row.pending_j + row.pending_s + row.pending_unclassified > 0 && <button type="button" className="ghost"
              onClick={() => onPick({ id: row.id, name: row.name })} aria-label={`查看${row.name}待审核题目`}>看待审题</button>}</td>
          </tr>)}</tbody>
        </table></div>
        {!rows.length && <p className="muted">没有符合条件的知识点。</p>}
      </>}
  </Modal>;
}
