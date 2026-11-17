import { useEffect, useState } from "react";
import {
  api, messageOf, type WrittenAttempt, type WrittenAttemptSummary, type WrittenAttemptView,
  type WrittenCatalogFilter, type WrittenPublishedSummary,
} from "../api";
import { readWrittenPosition, rememberWrittenPosition, type WrittenList, type WrittenTarget } from "../written-practice";
import { ErrorNotice } from "./RecordUI";
import { WrittenOpenAttempt, WrittenOpenQuestion, WrittenScoreBadge, writtenTypeLabel } from "./WrittenPracticeQuestion";
import { StudentKnowledge } from "./WrittenKnowledge";

const emptyFilter: WrittenCatalogFilter = { q: "", level: "", question_type: "" };
const listLabels: Record<WrittenList, string> = { bank: "题库", knowledge: "知识库", history: "作答记录", wrong: "待订正", corrected: "已订正" };
const emptyMessages: Record<Exclude<WrittenList, "knowledge">, string> = {
  bank: "暂无已发布题目。题目审核发布后会出现在这里。",
  history: "暂无作答记录。打开题库选一道题开始练习。",
  wrong: "暂无待订正的题目。",
  corrected: "暂无已订正的题目。错题重新答对后会出现在这里。",
};

export default function WrittenPractice({ userId }: { userId: number }) {
  const [position, setPosition] = useState(() => readWrittenPosition(userId));
  const { list, target } = position;
  const [receipt, setReceipt] = useState<WrittenAttempt>();
  const [filter, setFilter] = useState(emptyFilter);
  const [applied, setApplied] = useState(emptyFilter);
  const [catalog, setCatalog] = useState<WrittenPublishedSummary[]>([]);
  const [attempts, setAttempts] = useState<WrittenAttemptSummary[]>([]);
  const [offset, setOffset] = useState(0);
  const [cursors, setCursors] = useState<number[]>([]);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [hasNext, setHasNext] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const beforeId = cursors[cursors.length - 1];
  const inDetail = target !== null;
  const inBook = list === "wrong" || list === "corrected";

  useEffect(() => { rememberWrittenPosition(userId, position); }, [userId, position]);

  useEffect(() => {
    if (inDetail || list === "knowledge") return;
    const request = new AbortController();
    setLoading(true);
    setError("");
    setHasNext(false);
    setNextCursor(null);
    async function load() {
      try {
        if (list === "bank") {
          const result = await api.writtenCatalog(offset, applied, request.signal);
          if (request.signal.aborted) return;
          setCatalog(result.items);
          setHasNext(result.has_more);
        } else if (list !== "knowledge") {
          const result = await api.writtenAttempts(list, beforeId, request.signal);
          if (request.signal.aborted) return;
          setAttempts(result.items);
          setHasNext(result.has_more);
          setNextCursor(result.next_before_id);
        }
      } catch (failure) {
        if (!request.signal.aborted) setError(messageOf(failure));
      } finally {
        if (!request.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => request.abort();
  }, [list, inDetail, offset, applied, beforeId, reload]);

  function showList(next: WrittenList) {
    setPosition({ list: next, target: null });
    setReceipt(undefined);
    if (next !== list) {
      setOffset(0);
      setCursors([]);
    }
  }

  function open(next: WrittenTarget) {
    setReceipt(undefined);
    setPosition({ list, target: next });
  }

  function showResult(value: WrittenAttempt) {
    setReceipt(value);
    setPosition({ list, target: { kind: "attempt", id: value.id } });
  }

  return <section className="written-practice">
    <header className="page-head">
      <h1>初赛练习</h1>
      {target && <button className="ghost" type="button" onClick={() => showList(list)}>返回{listLabels[list]}</button>}
    </header>
    <nav className="admin-tabs" aria-label="初赛练习">
      <button className="ghost" type="button" aria-pressed={list === "bank"} onClick={() => showList("bank")}>题库</button>
      <button className="ghost" type="button" aria-pressed={list === "knowledge"} onClick={() => showList("knowledge")}>知识库</button>
      <button className="ghost" type="button" aria-pressed={list === "history"} onClick={() => showList("history")}>作答记录</button>
      <button className="ghost" type="button" aria-pressed={inBook} onClick={() => showList(inBook ? list : "wrong")}>错题本</button>
    </nav>
    {target?.kind === "question" && <WrittenOpenQuestion key={target.id} userId={userId} target={target}
      onVersion={(version) => setPosition({ list, target: { ...target, version } })} onSubmitted={showResult} />}
    {target?.kind === "attempt" && <WrittenOpenAttempt key={target.id} userId={userId} id={target.id} immediate={receipt}
      onRetryQuestion={(id) => open({ kind: "question", id })} />}
    {list === "knowledge" && <StudentKnowledge />}
    {!target && list !== "knowledge" && <div className="card written-practice-list">
      {list === "bank" && <form className="admin-actions written-practice-filters" onSubmit={(event) => {
        event.preventDefault();
        setOffset(0);
        setApplied({ ...filter, q: filter.q.trim() });
      }}>
        <label>题干或编号<input type="search" value={filter.q} maxLength={120} onChange={(event) => setFilter({ ...filter, q: event.target.value })} /></label>
        <label>级别<select value={filter.level} onChange={(event) => setFilter({ ...filter, level: event.target.value as WrittenCatalogFilter["level"] })}>
          <option value="">全部级别</option><option value="J">CSP-J</option><option value="S">CSP-S</option>
        </select></label>
        <label>题型<select value={filter.question_type} onChange={(event) => setFilter({ ...filter, question_type: event.target.value as WrittenCatalogFilter["question_type"] })}>
          <option value="">全部题型</option>
          {Object.entries(writtenTypeLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
        <button type="submit" className="btn-primary">查询</button>
      </form>}
      {inBook && <nav className="admin-tabs written-book-tabs" aria-label="错题订正状态">
        <button type="button" className="ghost" aria-pressed={list === "wrong"} onClick={() => showList("wrong")}>待订正</button>
        <button type="button" className="ghost" aria-pressed={list === "corrected"} onClick={() => showList("corrected")}>已订正</button>
      </nav>}
      {loading && <p role="status">正在读取{listLabels[list]}…</p>}
      {error && <ErrorNotice message={error} onRetry={() => setReload((value) => value + 1)} />}
      {!loading && !error && (list === "bank"
        ? <CatalogRows rows={catalog} filtered={Boolean(applied.q || applied.level || applied.question_type)}
          onRefresh={() => setReload((value) => value + 1)}
          onOpen={(row) => open({ kind: "question", id: row.id, version: row.version_id })} />
        : <AttemptRows rows={attempts} list={list} onBank={() => showList("bank")}
          onOpen={(id) => open({ kind: "attempt", id })} onRetry={(id) => open({ kind: "question", id })} />)}
      {(offset > 0 || cursors.length > 0 || hasNext) && <div className="written-practice-actions">
        <button className="ghost" type="button" disabled={loading || (!offset && !cursors.length)}
          onClick={() => list === "bank" ? setOffset(offset - 25) : setCursors(cursors.slice(0, -1))}>上一页</button>
        <button className="ghost" type="button" disabled={loading || !hasNext} onClick={() => {
          if (list === "bank") setOffset(offset + 25);
          else if (nextCursor !== null) setCursors([...cursors, nextCursor]);
        }}>下一页</button>
      </div>}
    </div>}
  </section>;
}

function CatalogRows({ rows, filtered, onOpen, onRefresh }: {
  rows: WrittenPublishedSummary[];
  filtered: boolean;
  onOpen: (row: WrittenPublishedSummary) => void;
  onRefresh: () => void;
}) {
  if (!rows.length) return <div className="written-empty">
    <p>{filtered ? "没有符合筛选条件的题目。" : emptyMessages.bank}</p>
    {!filtered && <button type="button" className="ghost" onClick={onRefresh}>刷新题库</button>}
  </div>;
  return <div className="table-scroll"><table>
    <thead><tr><th>题目</th><th>级别</th><th>题型</th><th>分值</th><th>操作</th></tr></thead>
    <tbody>{rows.map((row) => <tr key={row.id}>
      <td><span className="muted">#{row.id}</span><div className="written-excerpt">{row.stem}</div></td>
      <td>CSP-{row.level}</td><td>{writtenTypeLabel[row.question_type]}</td><td>{row.score}</td>
      <td><button type="button" className="ghost" onClick={() => onOpen(row)}>开始作答</button></td>
    </tr>)}</tbody>
  </table></div>;
}

function AttemptRows({ rows, list, onOpen, onRetry, onBank }: {
  rows: WrittenAttemptSummary[];
  list: WrittenAttemptView;
  onOpen: (id: number) => void;
  onRetry: (id: number) => void;
  onBank: () => void;
}) {
  if (!rows.length) return <div className="written-empty">
    <p>{emptyMessages[list]}</p><button type="button" className="ghost" onClick={onBank}>打开题库</button>
  </div>;
  return <div className="table-scroll"><table>
    <thead><tr><th>题目</th><th>结果</th><th>得分</th><th>提交时间</th><th>操作</th></tr></thead>
    <tbody>{rows.map((row) => <tr key={row.id}>
      <td><span className="muted">#{row.question_id} / v{row.revision}</span><div className="written-excerpt">{row.stem}</div></td>
      <td><WrittenScoreBadge correct={row.correct} /></td><td>{row.earned_score} / {row.max_score}</td>
      <td>{new Date(row.submitted_at).toLocaleString("zh-CN")}</td>
      <td><div className="written-practice-actions">
        <button type="button" className="ghost" onClick={() => onOpen(row.id)}>查看作答</button>
        {list === "wrong" && (row.is_available
          ? <button type="button" className="ghost" onClick={() => onRetry(row.question_id)}>重新作答</button>
          : <span className="muted">已下架</span>)}
      </div></td>
    </tr>)}</tbody>
  </table></div>;
}
