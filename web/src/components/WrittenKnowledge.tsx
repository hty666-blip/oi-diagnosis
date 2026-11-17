import { useEffect, useRef, useState } from "react";
import {
  api, messageOf, type AdminKnowledge, type AdminKnowledgeSummary,
  type KnowledgeArticle, type KnowledgeSummary, type PublishedKnowledge, type WrittenBatch, type WrittenPublishedSummary,
} from "../api";
import Markdown from "../Markdown";
import Modal from "./Modal";
import { ErrorNotice } from "./RecordUI";

const pageSize = 25;
const emptyArticle: KnowledgeArticle = { title: "", body: "", source: "" };
type KnowledgeRow = KnowledgeSummary | AdminKnowledgeSummary;
type DirectoryResult = { items: KnowledgeRow[]; has_more: boolean };
type OpenPractice = (question: WrittenPublishedSummary) => void;

export function StudentKnowledge({ active, onPractice }: { active: boolean; onPractice: OpenPractice }) {
  return <div hidden={!active}><KnowledgeDirectory admin={false} active={active} onPractice={onPractice} /></div>;
}

export default function AdminWrittenKnowledge() {
  return <KnowledgeDirectory admin />;
}

function KnowledgeDirectory({ admin, active = true, onPractice }: {
  admin: boolean; active?: boolean; onPractice?: OpenPractice;
}) {
  const [page, setPage] = useState<DirectoryResult>({ items: [], has_more: false });
  const [query, setQuery] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [reload, setReload] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<number | "new" | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const list = admin ? api.adminWrittenKnowledge : api.writtenKnowledge;
    void list(q, offset, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setPage(result); })
      .catch((failure: unknown) => { if (!controller.signal.aborted) setError(messageOf(failure)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [admin, q, offset, reload]);

  return <section className="card knowledge-directory">
    <div className="card-head">
      <h2>初赛知识库</h2>
      {admin && <button type="button" className="btn-primary" onClick={() => setSelected("new")}>新建知识条目</button>}
    </div>
    <form className="admin-actions written-practice-filters" onSubmit={(event) => {
      event.preventDefault();
      setOffset(0);
      setQ(query.trim());
      setReload((value) => value + 1);
    }}>
      <label>标题、知识点或编号<input type="search" maxLength={120} value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      <button type="submit" className="ghost" disabled={loading}>查询</button>
    </form>
    {loading && <p role="status">正在读取知识条目…</p>}
    {error && <ErrorNotice message={error} onRetry={() => setReload((value) => value + 1)} />}
    {!loading && !error && (page.items.length ? <div className="table-scroll"><table>
      <thead><tr><th>标题</th><th>知识点</th><th>{admin ? "发布状态" : "发布版本"}</th><th>操作</th></tr></thead>
      <tbody>{page.items.map((row) => <tr key={row.id}>
        <td>{row.title}</td><td>{row.point_name}<small>{row.point_id}</small></td>
        <td>{"is_active" in row
          ? (row.is_active ? `v${row.published_revision} 已上架` : row.published_revision ? "已下架" : "草稿")
          : `v${row.revision}`}</td>
        <td><button type="button" className="ghost" onClick={() => setSelected(row.id)}>{admin ? "编辑与审核" : "阅读"}</button></td>
      </tr>)}</tbody>
    </table></div> : <p>{q ? "没有匹配的知识条目。" : admin ? "暂无条目。可从已暂存题包的知识点新建。" : "暂无已发布的知识条目。"}</p>)}
    {(offset > 0 || page.has_more) && <div className="admin-actions">
      <button type="button" className="ghost" disabled={loading || offset === 0} onClick={() => setOffset(offset - pageSize)}>上一页</button>
      <button type="button" className="ghost" disabled={loading || !page.has_more || !!error} onClick={() => setOffset(offset + pageSize)}>下一页</button>
    </div>}
    {active && selected !== null && (admin
      ? <KnowledgeEditor entryId={selected === "new" ? undefined : selected} onClose={() => setSelected(null)}
        onChanged={() => setReload((value) => value + 1)} />
      : typeof selected === "number" && <KnowledgeReader id={selected} onClose={() => setSelected(null)} onPractice={onPractice} />)}
  </section>;
}

function ArticleText({ article }: { article: PublishedKnowledge }) {
  return <article className="knowledge-article">
    <p className="muted">{article.point_name} · v{article.revision}</p>
    <h3>{article.title}</h3>
    <Markdown text={article.body} />
    <p className="muted">资料来源　{article.source}</p>
  </article>;
}

function KnowledgeReader({ id, onClose, onPractice }: {
  id: number; onClose: () => void; onPractice?: OpenPractice;
}) {
  const [article, setArticle] = useState<PublishedKnowledge>();
  const [error, setError] = useState("");
  useEffect(() => {
    const request = new AbortController();
    void api.writtenKnowledgeArticle(id, request.signal)
      .then((value) => { if (!request.signal.aborted) setArticle(value); })
      .catch((failure: unknown) => { if (!request.signal.aborted) setError(messageOf(failure)); });
    return () => request.abort();
  }, [id]);
  return <Modal title="阅读知识条目" wide onClose={onClose} error={error}>
    {article ? <>
      <ArticleText article={article} />
      {onPractice && <section className="knowledge-practice">
        <h3>同知识点练习</h3>
        {article.related_questions?.length ? <ul>
          {article.related_questions.map((question) => <li key={question.id}>
            <div><div className="written-excerpt">{question.stem}</div><small className="muted">CSP-{question.level} · {question.score} 分</small></div>
            <button className="ghost" type="button" onClick={() => onPractice(question)}
              aria-label={`练习题目 #${question.id}`}>开始作答</button>
          </li>)}
        </ul> : <p className="muted">该知识点暂无已上架的练习。</p>}
      </section>}
    </> : !error && <p role="status">正在读取条目…</p>}
  </Modal>;
}

function KnowledgeEditor({ entryId, onClose, onChanged }: { entryId?: number; onClose: () => void; onChanged: () => void }) {
  const [entry, setEntry] = useState<AdminKnowledge>();
  const [article, setArticle] = useState(emptyArticle);
  const [batches, setBatches] = useState<WrittenBatch[]>([]);
  const [batchId, setBatchId] = useState("");
  const [points, setPoints] = useState<{ id: string; name: string }[]>([]);
  const [pointId, setPointId] = useState("");
  const [note, setNote] = useState("");
  const [edition, setEdition] = useState<(PublishedKnowledge & { review_note: string })>();
  const [loading, setLoading] = useState(true);
  const [topicsLoading, setTopicsLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const writeLock = useRef(false);
  const editionRequest = useRef<AbortController>();

  useEffect(() => {
    const request = new AbortController();
    const load = async () => {
      try {
        if (entryId !== undefined) {
          const detail = await api.adminKnowledgeArticle(entryId, request.signal);
          if (!request.signal.aborted) {
            setEntry(detail);
            setArticle(detail.article);
          }
        } else {
          const result = await api.writtenBatches(request.signal);
          if (!request.signal.aborted) setBatches(result.items.filter((batch) => batch.status === "staged"));
        }
      } catch (failure) {
        if (!request.signal.aborted) setError(messageOf(failure));
      } finally {
        if (!request.signal.aborted) setLoading(false);
      }
    };
    void load();
    return () => {
      request.abort();
      editionRequest.current?.abort();
    };
  }, [entryId]);

  useEffect(() => {
    if (!batchId) return;
    const request = new AbortController();
    setTopicsLoading(true);
    void api.writtenKnowledgeTopics(batchId, request.signal)
      .then((result) => { if (!request.signal.aborted) setPoints(result.items); })
      .catch((failure: unknown) => { if (!request.signal.aborted) setError(messageOf(failure)); })
      .finally(() => { if (!request.signal.aborted) setTopicsLoading(false); });
    return () => request.abort();
  }, [batchId]);

  async function save(action: "save" | "publish" | "unpublish") {
    if (writeLock.current) return;
    writeLock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = entry ? await api.editKnowledgeArticle(entry.id, {
        action, expected_version: entry.edit_version,
        ...(action !== "unpublish" ? { article } : {}),
        ...(action === "publish" ? { review_note: note } : {}),
      }) : await api.createKnowledgeArticle({ batch_id: batchId, point_id: pointId, article });
      setEntry(result);
      // 下架只修改可见性，保留尚未保存的输入。
      if (action !== "unpublish") setArticle(result.article);
      if (action === "publish") setNote("");
      setNotice(action === "publish" ? `已发布 v${result.published_revision}。`
        : action === "unpublish" ? "已下架。" : "编辑稿已保存。");
      onChanged();
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      writeLock.current = false;
      setBusy(false);
    }
  }

  async function readEdition(revision: number) {
    if (!entry) return;
    editionRequest.current?.abort();
    setEdition(undefined);
    if (!revision) return;
    const request = new AbortController();
    editionRequest.current = request;
    try {
      const result = await api.adminKnowledgeEdition(entry.id, revision, request.signal);
      if (!request.signal.aborted) setEdition(result);
    } catch (failure) {
      if (!request.signal.aborted) setError(messageOf(failure));
    }
  }

  const ready = !busy && !!article.title.trim() && (!!entry || !!pointId);
  return <Modal title={entry ? "编辑与审核知识条目" : "新建知识条目"} wide busy={busy} onClose={onClose} error={error}>
    {loading && <p role="status">正在读取条目与目录…</p>}
    {notice && <p role="status">{notice}</p>}
    {!loading && (entry !== undefined || entryId === undefined) &&
      <form className="admin-form" onSubmit={(event) => { event.preventDefault(); if (ready) void save("save"); }}>
        {entry ? <p className="muted">{entry.point_name} · {entry.point_id}{entry.is_active && ` · v${entry.published_revision} 生效中`}</p> : <>
          <label>题包批次
            <select value={batchId} disabled={busy} onChange={(event) => {
              setBatchId(event.target.value);
              setPoints([]);
              setPointId("");
              setTopicsLoading(!!event.target.value);
              setError("");
            }}><option value="">{batches.length ? "选择已暂存批次" : "暂无已暂存批次"}</option>
              {batches.map((batch) => <option key={batch.id} value={batch.id}>
                {new Date(batch.created_at).toLocaleString("zh-CN")} · {batch.summary.questions} 题 · {batch.id.slice(0, 8)}
              </option>)}
            </select>
          </label>
          <label>知识点
            <select value={pointId} disabled={busy || topicsLoading || !batchId} onChange={(event) => {
              const point = points.find((item) => item.id === event.target.value);
              setPointId(event.target.value);
              if (point && !article.title.trim()) setArticle({ ...article, title: point.name.slice(0, 120) });
            }}><option value="">{topicsLoading ? "正在读取知识点…" : "选择叶子知识点"}</option>
              {points.map((point) => <option value={point.id} key={point.id}>{point.name} · {point.id}</option>)}
            </select>
          </label>
        </>}
        <label>标题<input value={article.title} maxLength={120} disabled={busy} onChange={(event) => setArticle({ ...article, title: event.target.value })} /></label>
        <label>知识正文<textarea rows={9} value={article.body} maxLength={3800} disabled={busy} onChange={(event) => setArticle({ ...article, body: event.target.value })} /></label>
        <label>资料来源<input value={article.source} maxLength={500} disabled={busy} onChange={(event) => setArticle({ ...article, source: event.target.value })} /></label>
        {entry && <label>审核说明<input value={note} maxLength={400} disabled={busy} onChange={(event) => setNote(event.target.value)} /></label>}
        {entry?.is_active && <p className="muted">保存编辑稿后，学生仍看到已发布的版本。</p>}
        <div className="admin-actions">
          <button type="submit" className={entry ? "ghost" : "btn-primary"} disabled={!ready}>保存编辑稿</button>
          {entry && <button type="button" className="btn-primary" disabled={!ready || note.trim().length < 4}
            onClick={() => void save("publish")}>审核并发布</button>}
          {entry?.is_active && <button type="button" className="ghost" disabled={busy} onClick={() => void save("unpublish")}>下架条目</button>}
        </div>
        {!!entry?.editions.length && <label>历次发布内容
          <select defaultValue="" onChange={(event) => void readEdition(Number(event.target.value))}>
            <option value="">选择要查看的版本</option>
            {entry.editions.map((item) => <option key={item.revision} value={item.revision}>v{item.revision} · {item.title}</option>)}
          </select>
        </label>}
      </form>}
    {edition && <div className="knowledge-edition"><ArticleText article={edition} /><p className="muted">审核说明　{edition.review_note}</p></div>}
  </Modal>;
}
