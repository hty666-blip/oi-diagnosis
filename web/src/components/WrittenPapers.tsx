import { useEffect, useState } from "react";
import { api, messageOf, type WrittenPaperQuestions, type WrittenPaperSummary, type WrittenPublishedSummary } from "../api";
import { ErrorNotice } from "./RecordUI";
import { writtenTypeLabel } from "./WrittenPracticeQuestion";

const sourceLabels = { official_paper: "真题", simulation: "模拟题", original_demo: "自编演示" };

export default function WrittenPapers({ active, onPractice }: {
  active: boolean;
  onPractice: (question: WrittenPublishedSummary) => void;
}) {
  const [papers, setPapers] = useState<{ items: WrittenPaperSummary[]; has_more: boolean }>();
  const [selected, setSelected] = useState<WrittenPaperSummary>();
  const [detail, setDetail] = useState<WrittenPaperQuestions>();
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const refresh = () => setReload((value) => value + 1);
  const hasMore = selected ? detail?.has_more : papers?.has_more;

  useEffect(() => {
    if (!active) return;
    const request = new AbortController();
    setError("");
    setLoading(true);
    const load = selected
      ? api.writtenPaperQuestions(selected.batch_id, selected.id, offset, request.signal).then((value) => {
        if (!request.signal.aborted) setDetail(value);
      })
      : api.writtenPapers(offset, request.signal).then((value) => {
        if (!request.signal.aborted) setPapers(value);
      });
    void load.catch((failure) => { if (!request.signal.aborted) setError(messageOf(failure)); })
      .finally(() => { if (!request.signal.aborted) setLoading(false); });
    return () => request.abort();
  }, [active, offset, selected, reload]);

  function select(paper?: WrittenPaperSummary) {
    setDetail(undefined);
    setOffset(0);
    setSelected(paper);
  }

  return <div hidden={!active}><section className="card written-practice-list">
    <div className="card-head">
      <h2>{selected?.title ?? "按试卷练习"}</h2>
      {selected && <button className="ghost" type="button" onClick={() => select()}>返回试卷列表</button>}
    </div>
    {error && <ErrorNotice message={error} onRetry={refresh} />}
    {loading && !error && <p role="status">正在读取试卷…</p>}
    {!error && !loading && (selected && detail ? <>
      <p className="muted">已上架 {detail.paper.published_count} / 收录 {detail.paper.candidate_count} 题。逐题作答，提交后可看解析。</p>
      <div className="table-scroll"><table>
        <thead><tr><th>题号</th><th>题目</th><th>题型</th><th>分值</th><th>操作</th></tr></thead>
        <tbody>{detail.items.map((question) => <tr key={question.id}>
          <td>{question.number}</td><td><div className="written-excerpt">{question.stem}</div></td>
          <td>{writtenTypeLabel[question.question_type]}</td><td>{question.score}</td>
          <td><button className="ghost" type="button" onClick={() => onPractice(question)}>开始作答</button></td>
        </tr>)}</tbody>
      </table></div>
    </> : papers?.items.length ? <div className="table-scroll"><table>
      <thead><tr><th>试卷名称</th><th>级别</th><th>来源类型</th><th>已上架 / 收录</th><th>操作</th></tr></thead>
      <tbody>{papers.items.map((paper) => <tr key={`${paper.batch_id}:${paper.id}`}>
        <td>{paper.title}{paper.year && <small className="muted"> · {paper.year}</small>}</td>
        <td>{paper.level === "J" ? "J · 入门" : paper.level === "S" ? "S · 提高" : "未分级"}</td>
        <td>{paper.source_kind ? sourceLabels[paper.source_kind] : "未标注"}</td>
        <td>{paper.published_count} / {paper.candidate_count}</td>
        <td><button className="ghost" type="button" onClick={() => select(paper)}>查看试卷</button></td>
      </tr>)}</tbody>
    </table></div> : <div className="written-empty"><p>暂无包含上架题目的试卷。</p>
      <button type="button" className="ghost" onClick={refresh}>刷新试卷</button>
    </div>)}
    {(offset > 0 || hasMore) && <div className="written-practice-actions">
      <button type="button" className="ghost" disabled={loading || offset === 0} onClick={() => setOffset(offset - 25)}>上一页</button>
      <button type="button" className="ghost" disabled={loading || !hasMore || !!error} onClick={() => setOffset(offset + 25)}>下一页</button>
    </div>}
  </section></div>;
}
