import { useEffect, useRef, useState } from "react";
import { api, messageOf, type WrittenBatch, type WrittenDetail, type WrittenPage } from "../api";
import Markdown from "../Markdown";
import Modal from "./Modal";
import { ErrorNotice } from "./RecordUI";

const types: Record<string, string> = { single_choice: "单选", multiple_choice: "多选", true_false: "判断", fill_blank: "填空" };
const flags: Record<string, string> = {
  answer_unverified: "答案未核对", answer_conflict: "答案冲突", scoring_rule_unknown: "计分规则待确认",
  deduplication_requires_review: "去重关系待核对", withdrawn_question_requires_review: "撤题线索",
  knowledge_tag_suggestion: "知识点修订建议", knowledge_point_unclassified: "知识点未分类",
  possibly_out_of_syllabus: "大纲范围待核对", code_in_image: "程序保存在图片中",
};
const label = (value: string) => flags[value] ?? value;
const displayText = (text: string) => text.split("```").map((part, i) => i % 2 ? part : part.replace(/!\[[^\]]*\]\(assets\/[^)\n]+\)/g, "")).join("```");
function answerLabel(value: unknown): string {
  if (!value || typeof value !== "object") return "待核对";
  const answer = value as { kind?: string; value?: boolean; option_ids?: string[]; accepted?: string[]; raw?: string };
  if (typeof answer.value === "boolean") return answer.value ? "正确" : "错误";
  if (Array.isArray(answer.option_ids)) return answer.option_ids.join("、");
  if (Array.isArray(answer.accepted)) return answer.accepted.join(" 或 ");
  if (typeof answer.raw === "string") return answer.raw;
  return "待核对";
}

function CandidateImages({ batchId, paths }: { batchId: string; paths: string[] }) {
  const [images, setImages] = useState<{ path: string; url: string }[]>([]);
  const [error, setError] = useState("");
  const key = paths.join("\n");
  useEffect(() => {
    const controller = new AbortController(), urls: string[] = [];
    setImages([]); setError("");
    void Promise.all(paths.map(async (path) => {
      const blob = await api.writtenAsset(batchId, path, controller.signal);
      if (controller.signal.aborted) return null;
      const url = URL.createObjectURL(blob); urls.push(url); return { path, url };
    })).then((items) => { if (!controller.signal.aborted) setImages(items.filter((v): v is { path: string; url: string } => !!v)); })
      .catch((e) => { if (!controller.signal.aborted) setError(messageOf(e)); });
    return () => { controller.abort(); urls.forEach((url) => URL.revokeObjectURL(url)); };
  }, [batchId, key]);
  return <>{error && <ErrorNotice message={error} />}{images.map((img, i) => <figure key={img.path}><img className="written-asset" src={img.url} alt={`候选题原始图片 ${i + 1}`} /><figcaption className="muted">题目原图 {i + 1}</figcaption></figure>)}</>;
}

export default function WrittenBank() {
  const [batches, setBatches] = useState<WrittenBatch[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [page, setPage] = useState<WrittenPage>();
  const [loading, setLoading] = useState(false), [busy, setBusy] = useState(false);
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [importing, setImporting] = useState(false), [file, setFile] = useState<File>();
  const [query, setQuery] = useState(""), [paper, setPaper] = useState(""), [flagged, setFlagged] = useState(false);
  const [offset, setOffset] = useState(0);
  const [detail, setDetail] = useState<WrittenDetail>(), [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const listRequest = useRef<AbortController>(), rowRequest = useRef<AbortController>(), detailRequest = useRef<AbortController>();
  const uploadRequest = useRef<AbortController>(), lock = useRef(false);
  const applied = useRef({ q: "", p: "", f: false });
  async function loadBatches() {
    listRequest.current?.abort(); const controller = new AbortController(); listRequest.current = controller;
    try { const value = await api.writtenBatches(controller.signal); setBatches(value.items); setTruncated(value.has_more); }
    catch (e) { if (!controller.signal.aborted) setError(messageOf(e)); }
  }
  useEffect(() => { void loadBatches(); return () => { listRequest.current?.abort(); rowRequest.current?.abort(); detailRequest.current?.abort(); uploadRequest.current?.abort(); }; }, []);
  async function load(id: string, next = 0, q = query, p = paper, f = flagged) {
    rowRequest.current?.abort(); const controller = new AbortController(); rowRequest.current = controller;
    setLoading(true); setError("");
    try { const value = await api.writtenQuestions(id, next, q, p, f, controller.signal); setPage(value); setOffset(next); applied.current = { q, p, f }; }
    catch (e) { if (!controller.signal.aborted) setError(messageOf(e)); }
    finally { if (!controller.signal.aborted) setLoading(false); }
  }
  async function upload() {
    if (!file || lock.current) return;
    lock.current = true; setBusy(true); setError(""); setNotice("");
    const controller = new AbortController(); uploadRequest.current = controller;
    try {
      const value = await api.writtenPreview(file, controller.signal);
      setImporting(false); setFile(undefined); setQuery(""); setPaper(""); setFlagged(false);
      setNotice(value.replayed ? "已恢复这份题包的导入记录。" : "预览已保存，请核对内容后暂存。预览保留 24 小时。");
      await loadBatches(); await load(value.id, 0, "", "", false);
    } catch (e) { if (!controller.signal.aborted) setError(messageOf(e)); }
    finally { lock.current = false; if (!controller.signal.aborted) setBusy(false); }
  }
  async function stage() {
    if (!page || lock.current) return;
    lock.current = true; setBusy(true); setError(""); setNotice("");
    try {
      const value = await api.writtenStage(page.batch.id);
      setPage({ ...page, batch: value }); setNotice(`已暂存 ${value.summary.questions} 道候选题，全部待审核。`);
      await loadBatches();
    } catch (e) { setError(messageOf(e)); }
    finally { lock.current = false; setBusy(false); }
  }
  async function openQuestion(id: string) {
    if (!page) return;
    detailRequest.current?.abort(); const controller = new AbortController(); detailRequest.current = controller;
    setDetail(undefined); setDetailLoading(true); setDetailError("");
    try { setDetail(await api.writtenQuestion(page.batch.id, id, controller.signal)); }
    catch (e) { if (!controller.signal.aborted) setDetailError(messageOf(e)); }
    finally { if (!controller.signal.aborted) setDetailLoading(false); }
  }
  const disabled = busy || loading;
  const detailOpen = detailLoading || !!detail || !!detailError;
  const knowledgeName = (id: string) => detail?.knowledge_points.find((n) => n.id === id)?.name ?? id;
  return <section className="card written-bank">
    <div className="page-head"><h2>初赛题库</h2><div className="admin-actions"><button className="ghost" disabled={disabled} onClick={() => void loadBatches()}>刷新批次</button><button className="btn-primary" disabled={disabled} onClick={() => { setImporting(true); setError(""); }}>导入 ZIP 题包</button></div></div>
    {error && !importing && <ErrorNotice message={error} />}{notice && <p role="status">{notice}</p>}
    <div className="table-scroll"><table><thead><tr><th>导入时间</th><th>试卷 / 题目</th><th>状态</th><th>操作</th></tr></thead><tbody>{batches.map((b) => <tr key={b.id}><td>{new Date(b.created_at).toLocaleString("zh-CN")}</td><td>{b.summary.papers} 套 / {b.summary.questions} 题</td><td>{b.status === "staged" ? "已暂存 · 待审核" : "待确认预览"}</td><td><button className="ghost" disabled={disabled} onClick={() => { setQuery(""); setPaper(""); setFlagged(false); void load(b.id, 0, "", "", false); }}>查看</button></td></tr>)}</tbody></table></div>
    {!batches.length && <p className="muted">暂无导入记录。支持包含题目、题组、试卷、知识目录、来源及清单的 ZIP 包。</p>}{truncated && <p className="muted">显示最近 50 个批次。</p>}
    {page && <section className="admin-section">
      <div className="page-head"><h3>{page.batch.status === "staged" ? "待审核候选题" : "导入预览"}</h3>{page.batch.status === "preview" && <button className="btn-primary" disabled={disabled} onClick={() => void stage()}>确认暂存 {page.batch.summary.questions} 道题</button>}</div>
      <p>{page.batch.summary.questions} 条原始题目 · {page.batch.summary.groups} 个题组 · {page.batch.summary.assets} 张图片 · {page.batch.summary.knowledge_leaves} 个知识点</p>
      <p className="muted">保留独立题目，不按来源去重编号合并。全部进入待审核区，暂存不会发布给学生。</p>
      <details><summary>复核事项</summary><dl className="written-flags">{Object.entries(page.batch.summary.flags).map(([key, count]) => <div key={key}><dt>{label(key)}</dt><dd>{count}</dd></div>)}</dl></details>
      <form className="admin-actions written-filter" onSubmit={(e) => { e.preventDefault(); void load(page.batch.id, 0); }}>
        <label>题目或编号<input value={query} maxLength={120} onChange={(e) => setQuery(e.target.value)} disabled={disabled} /></label>
        <label>试卷<select value={paper} onChange={(e) => setPaper(e.target.value)} disabled={disabled}><option value="">全部试卷</option>{page.papers.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}</select></label>
        <label className="catalog-check"><input type="checkbox" checked={flagged} onChange={(e) => setFlagged(e.target.checked)} disabled={disabled} />仅看有复核标记的题</label><button className="ghost" disabled={disabled}>查询</button>
      </form>
      {loading && <p role="status">正在加载候选题…</p>}
      <div className="table-scroll"><table><thead><tr><th>编号 / 题干</th><th>级别 / 题型</th><th>复核标记</th><th>操作</th></tr></thead><tbody>{page.items.map((q) => <tr key={q.external_id}><td><div className="muted">{q.external_id}</div><div className="written-excerpt">{q.stem}</div></td><td>{q.level ?? "未分组"} · {types[q.question_type] ?? q.question_type}</td><td>{q.issues.length ? q.issues.map(label).join("、") : "待逐题核对"}</td><td><button className="ghost" disabled={disabled} onClick={() => void openQuestion(q.external_id)}>核对题目</button></td></tr>)}</tbody></table></div>
      {!page.items.length && <p>没有符合条件的题目。</p>}
      <div className="admin-actions"><span>共 {page.total} 条，当前 {page.items.length ? offset + 1 : 0}–{offset + page.items.length}</span><button className="ghost" disabled={disabled || offset === 0} onClick={() => void load(page.batch.id, Math.max(0, offset - 25), applied.current.q, applied.current.p, applied.current.f)}>上一页</button><button className="ghost" disabled={disabled || !page.has_more} onClick={() => void load(page.batch.id, offset + 25, applied.current.q, applied.current.p, applied.current.f)}>下一页</button></div>
    </section>}
    {importing && <Modal title="导入初赛题包" busy={busy} error={error} onClose={() => { setImporting(false); setFile(undefined); setError(""); }}>
      <label className="admin-form">ZIP 题包<input type="file" accept=".zip,application/zip" disabled={busy} onChange={(e) => { const next = e.target.files?.[0]; setFile(undefined); setError(""); if (next && next.size > 16 * 1024 * 1024) setError("ZIP 题包不得超过 16 MB。"); else setFile(next); }} /></label>
      <p className="muted">先校验清单、图片和题组关系，再保存预览。审核状态与来源记录原样保留。</p>
      <div className="admin-actions"><button className="btn-primary" disabled={!file || busy} onClick={() => void upload()}>校验并预览</button><button className="ghost" disabled={busy} onClick={() => { setImporting(false); setFile(undefined); setError(""); }}>取消</button></div>
    </Modal>}
    {detailOpen && <Modal title="核对候选题" wide error={detailError} onClose={() => { detailRequest.current?.abort(); setDetail(undefined); setDetailLoading(false); setDetailError(""); }}>
      {detailLoading && <p role="status">正在加载题目…</p>}{detail && page && <>
        <p className="muted">{detail.record.id} · 待审核</p>
        {!!detail.normalized.issues.length && <p className="notice notice-warn">{detail.normalized.issues.map(label).join("、")}</p>}
        {(detail.record.shared_stem || detail.group?.shared_stem) && <section><h3>共用材料</h3><Markdown text={displayText(detail.record.shared_stem || detail.group?.shared_stem || "")} /></section>}
        <h3>题目</h3><Markdown text={displayText(detail.record.stem)} />{detail.record.options?.map((o) => <div className="written-option" key={o.id}><strong>{o.id}.</strong><Markdown text={displayText(o.text)} /></div>)}
        <CandidateImages batchId={page.batch.id} paths={[...new Set([...(detail.record.asset_paths ?? []), ...(detail.group?.asset_paths ?? [])])]} />
        <h3>来源提供的答案</h3><pre>{answerLabel(detail.record.answer)}</pre>
        {!!detail.record.answer_alternatives?.length && <><h3>其他来源的答案</h3>{detail.record.answer_alternatives.map((a, i) => <p key={i}>{answerLabel(a)} · {detail.sources.find((s) => s.id === a.source_id)?.title ?? a.note ?? "来源待核对"}</p>)}</>}
        <h3>来源标注的知识点</h3><p>{[detail.record.primary_knowledge_point_id, ...(detail.record.secondary_knowledge_point_ids ?? [])].filter((v): v is string => !!v).map(knowledgeName).join("、") || "未标注"}</p>
        {!!detail.normalized.knowledge_suggestions.length && <><h3>待核对的标注建议</h3>{detail.normalized.knowledge_suggestions.map((n) => <p key={n.id}>{n.name} · {n.basis}</p>)}</>}
        {detail.normalized.related_question_id && <p>来源关联题：{detail.normalized.related_question_id}，当前仍保留为独立候选题。</p>}
        <h3>来源定位</h3><p className="written-locator">{detail.record.source_locator || "待补充"}</p>
        <details><summary>来源与使用说明</summary>{detail.sources.filter((s) => [...(detail.record.source_ids ?? []), detail.record.answer_source_id].includes(s.id)).map((s) => <section key={s.id}><h4>{s.title}</h4>{s.url && /^https?:\/\//i.test(s.url) && <a href={s.url} target="_blank" rel="noreferrer">打开来源</a>}<p>{s.license || "使用范围待核对"}</p></section>)}</details>
        <details><summary>原始记录</summary><pre className="written-raw">{JSON.stringify(detail.record, null, 2)}</pre></details>
      </>}
    </Modal>}
  </section>;
}
