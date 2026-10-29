import { useEffect, useState } from "react";
import { api, messageOf, type LineageItem } from "../api";
import { ErrorNotice, VerdictBadge } from "./RecordUI";

export default function SubmissionLineage({ submissionId, onOpen }: { submissionId: number; onOpen: (id: number) => void }) {
  const [rows, setRows] = useState<LineageItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true); setError(""); setRows([]);
    api.lineage(submissionId).then((value) => { if (active) setRows(value); })
      .catch((e) => { if (active) setError(messageOf(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [submissionId, retry]);
  return <section className="submission-lineage" aria-label="本次修改记录"><h3>本次修改记录</h3>
    {loading && <p role="status" className="muted">正在读取提交版本…</p>}
    <ErrorNotice message={error} onRetry={() => setRetry((value) => value + 1)} />
    {!loading && !error && rows.length === 1 && <p className="muted">这是本次修改的起点。继续修改并重新提交后，会保留前后版本关系。</p>}
    <ol className="lineage-list">{rows.map((row) => <li key={row.id}>
      <div className="row"><button className="ghost" onClick={() => onOpen(row.id)} disabled={row.id === submissionId}>#{row.id}{row.id === submissionId ? " · 当前记录" : " · 打开代码"}</button><VerdictBadge verdict={row.verdict} tests={row.tests_passed} /></div>
      <details><summary>查看该版本代码</summary><pre className="review-code"><code>{row.code}</code></pre></details>
    </li>)}</ol>
  </section>;
}
