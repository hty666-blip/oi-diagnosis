import { useEffect, useRef, useState } from "react";
import { api, messageOf, type CatalogPreview, type CatalogProblem, type CatalogVersion } from "../api";
import { ErrorNotice } from "./RecordUI";

const template = {
  problems: [{ pid: "DEMO-SUM", title: "示例：整数求和", statement_md: "读取两个整数，输出它们的和。",
    samples: [{ input: "1 2\n", output: "3\n" }], difficulty: "入门", tags: ["基础运算"], knowledge_points: [],
    package_level: "A", source: "self-authored", source_url: "", license_note: "请填写实际许可或自建题授权说明",
    reviewer: "请填写实际审核人", time_limit_ms: 2000, memory_limit_mb: 256,
    cases: [{ name: "positive", input: "1 2\n", output: "3\n" }, { name: "negative", input: "-5 1\n", output: "-4\n" }, { name: "zero", input: "0 0\n", output: "0\n" }] }],
};
const actionLabel = { create: "新增待上架题目", new_version: "追加版本并下架", unchanged: "内容相同，保留状态" };

export default function ProblemCatalog() {
  const [items, setItems] = useState<CatalogProblem[]>([]);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState("all");
  const [offset, setOffset] = useState(0);
  const [more, setMore] = useState(false);
  const [local, setLocal] = useState<Awaited<ReturnType<typeof api.catalogLocalPackages>>>([]);
  const [pids, setPids] = useState<string[]>([]);
  const [content, setContent] = useState("");
  const [rights, setRights] = useState(false);
  const [preview, setPreview] = useState<CatalogPreview>();
  const [selected, setSelected] = useState<CatalogProblem>();
  const [versions, setVersions] = useState<CatalogVersion[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const lock = useRef(false), generation = useRef(0);
  const importArea = useRef<HTMLElement>(null);

  async function load(page = offset) {
    const id = ++generation.current; setLoading(true); setError("");
    try {
      const [bank, packages] = await Promise.all([api.catalogProblems(q, page, filter), api.catalogLocalPackages()]);
      if (id !== generation.current) return;
      setItems(bank.items); setMore(bank.has_more); setOffset(page); setLocal(packages);
    } catch (e) { if (id === generation.current) setError(messageOf(e)); }
    finally { if (id === generation.current) setLoading(false); }
  }
  useEffect(() => { void load(0); return () => { generation.current++; }; }, []);
  async function run(action: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (e) { setError(messageOf(e)); }
    finally { lock.current = false; setBusy(false); }
  }
  async function select(row: CatalogProblem) {
    await run(async () => { const history = await api.catalogHistory(row.id); setSelected(row); setVersions(history.items); setTruncated(history.truncated); });
  }
  const disabled = busy || loading;

  return <div className="problem-catalog">
    <div className="card-head"><div><h2>题库管理</h2><p className="muted">校验后导入，核对来源与测试，再上架供新训练和自由练习选择。</p></div><button className="ghost" disabled={disabled} onClick={() => { setSelected(undefined); void load(); }}>刷新题库</button></div>
    <ErrorNotice message={error} onRetry={() => void load()} />
    {notice && <p className="session-notice" role="status">{notice}</p>}
    <section className="card">
      <form className="admin-actions" onSubmit={(e) => { e.preventDefault(); setSelected(undefined); void load(0); }}>
        <label htmlFor="catalog-query">题号或标题</label><input id="catalog-query" maxLength={120} value={q} onChange={(e) => setQ(e.target.value)} disabled={disabled} />
        <label>上架状态<select value={filter} onChange={(e) => setFilter(e.target.value)} disabled={disabled}><option value="all">全部题目</option><option value="active">已上架</option><option value="inactive">待上架 / 已下架</option></select></label><button className="ghost" disabled={disabled}>查询题库</button>
      </form>
      {loading && <p role="status">正在读取题库…</p>}
      <div className="table-scroll"><table><thead><tr><th>题目</th><th>版本 / 状态</th><th>评测范围</th><th>来源与校验</th><th>操作</th></tr></thead><tbody>{items.map((p) => <tr key={p.id}><td>{p.pid}<div>{p.title}</div></td><td>v{p.revision || "待接管"}<div>{p.is_active ? "已上架" : "未上架"}</div></td><td>{p.package_level} 级 · {p.scope === "package" ? "正式测试" : p.scope === "samples" ? "公开样例" : "评测不可用"} {p.cases ?? "待校验"}<div className="muted">公开样例 {p.samples}</div></td><td>{p.source || "来源待补齐"}{p.notes.map((n) => <div className="muted" key={n}>{n}</div>)}</td><td><button className="ghost" disabled={disabled} onClick={() => void select(p)}>管理 {p.pid}</button></td></tr>)}</tbody></table></div>
      {!items.length && !loading && <p className="muted">暂无匹配题目。可调整筛选，或在下方导入题包。</p>}
      <div className="admin-actions"><button className="ghost" disabled={disabled || offset === 0} onClick={() => void load(Math.max(0, offset - 50))}>上一页题目</button><span>第 {Math.floor(offset / 50) + 1} 页</span><button className="ghost" disabled={disabled || !more} onClick={() => void load(offset + 50)}>下一页题目</button></div>
    </section>
    {selected && <section className="card">
      <div className="card-head"><h2>{selected.pid} · v{selected.revision}</h2><button className="ghost" disabled={disabled} onClick={() => setSelected(undefined)}>收起题目管理</button></div>
      <p>{selected.title}</p><dl className="admin-status"><div><dt>来源</dt><dd>{selected.source || "待补齐"}{selected.source_url && /^https?:\/\//i.test(selected.source_url) && <> · <a href={selected.source_url} target="_blank" rel="noreferrer">查看来源页面</a></>}</dd></div><div><dt>许可说明</dt><dd>{selected.license_note || "待补齐，不能重新上架"}</dd></div></dl>
      <p className="muted">下架会阻止新选题与新的自由练习；已发布训练、历史提交修改及其代码讲解继续使用原版本。草稿在发布时需核对为当前已上架版本。</p>
      <button className={selected.is_active ? "ghost" : "btn-primary"} disabled={disabled} onClick={() => void run(async () => { const next = await api.catalogState(selected.id, selected.catalog_version, !selected.is_active); setSelected(next); setNotice(next.is_active ? "当前版本已上架。" : "题目已下架，历史记录和已发布训练未改变。"); await load(); })}>{selected.is_active ? "下架此题" : `上架 v${selected.revision}`}</button>
      <h3>版本记录</h3><p className="muted">版本只追加。历史题面、测试与资源限制保持快照；导入相同内容不会重复追加。</p>
      <div className="table-scroll"><table><thead><tr><th>版本 / 时间</th><th>标题</th><th>测试 / 审核人</th><th>操作</th></tr></thead><tbody>{versions.map((v) => <tr key={v.id}><td>v{v.revision}<div className="muted">{new Date(v.created_at).toLocaleString("zh-CN")}</div></td><td>{v.title}</td><td>{v.cases ?? "快照不可用"} · {v.reviewer || "旧记录 / 样例"}<div className="muted">{v.created_by == null ? "迁移或系统导入" : `管理员 #${v.created_by}`}</div></td><td><button className="ghost" disabled={disabled} onClick={() => void run(async () => { const value = await api.catalogVersion(selected.id, v.id); setContent(JSON.stringify({ problems: [value.problem] }, null, 2)); setPreview(undefined); setRights(false); setNotice(value.snapshot_available ? "版本已载入导入区。修改并校验后才会追加新版本。" : "旧版本缺少测试快照，请补齐数据后再校验。"); importArea.current?.scrollIntoView({ behavior: "smooth", block: "start" }); })}>载入 v{v.revision} 到导入区</button></td></tr>)}</tbody></table></div>
      {truncated && <p className="muted">仅显示最近 30 个版本；更早版本仍保留供已绑定记录读取。</p>}
    </section>}
    <section className="card" ref={importArea}>
      <h2>导入题包</h2><p className="muted">支持 UTF-8 JSON 题包，每批最多 20 题、8 MB。A/B 需正式测试与审核人，C/D 仅使用公开样例。导入新版本会先下架，原训练和提交不会被替换。</p>
      <button className="ghost" disabled={disabled} onClick={() => { const url = URL.createObjectURL(new Blob([JSON.stringify(template, null, 2)], { type: "application/json;charset=utf-8" })); const a = document.createElement("a"); a.href = url; a.download = "题包导入模板.json"; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }}>下载 JSON 题包模板</button>
      <label className="admin-file">选择 JSON 题包<input type="file" accept=".json,application/json" disabled={disabled} onChange={async (e) => { const file = e.target.files?.[0]; e.target.value = ""; if (!file) return; setPreview(undefined); setRights(false); if (file.size > 8_000_000) { setError("文件不得超过 8 MB。"); return; } try { setContent(await file.text()); } catch { setError("文件读取失败，请重新选择 UTF-8 JSON 文件。"); } }} /></label>
      <label className="admin-form">JSON 题包内容<textarea rows={10} spellCheck={false} value={content} maxLength={8_000_000} onChange={(e) => { setContent(e.target.value); setPreview(undefined); setRights(false); }} disabled={disabled} /></label>
      <label className="catalog-rights"><input type="checkbox" checked={rights} onChange={(e) => { setRights(e.target.checked); setPreview(undefined); }} disabled={disabled} />我已核对题面和评测数据的来源、许可及使用权限</label>
      <button className="ghost" disabled={disabled || !content.trim() || !rights} onClick={() => void run(async () => { setPreview(undefined); let input; try { input = JSON.parse(content.replace(/^\uFEFF/, "")); } catch { throw new Error("JSON 格式不正确，请使用模板核对引号、逗号和括号。"); } const rows = Array.isArray(input) ? input : input?.problems; if (!Array.isArray(rows)) throw new Error("题包需包含 problems 数组。"); setPreview(await api.catalogPreview(rows, rights)); })}>校验并预览 JSON</button>
      <details className="admin-section"><summary>从仓库导入已审阅的自建题包</summary><p className="muted">读取仓库 problems 中的元数据、题面及成对测试文件，校验失败的题包不能选择。预览会固定本次内容。</p><fieldset disabled={disabled}><legend>仓库题包</legend>{local.map((p) => <label className="catalog-check" key={p.pid}><input type="checkbox" disabled={!p.valid} checked={pids.includes(p.pid)} onChange={(e) => { setPids(e.target.checked ? [...pids, p.pid] : pids.filter((id) => id !== p.pid)); setPreview(undefined); }} />{p.pid} · {p.title} · {p.valid ? `${p.package_level} 级 / ${p.cases} 个测试` : "校验未通过"}</label>)}{!local.length && <p>仓库中没有可导入题包。</p>}</fieldset><button className="ghost" disabled={disabled || !pids.length || !rights} onClick={() => void run(async () => { setPreview(undefined); setPreview(await api.catalogLocalPreview(pids, rights)); })}>校验所选仓库题包</button></details>
    </section>
    {preview && <section className="card"><h2>导入预览</h2><p>{preview.notice}</p><p className="muted">预览有效至 {new Date(preview.expires_at).toLocaleString("zh-CN")}，确认时重新核对题库状态；冲突会整批拒绝。</p>
      {preview.rows.map((p) => <article className="catalog-preview-row" key={p.pid}><h3>{p.pid} · {p.title}</h3><p>{actionLabel[p.action]}{p.existing_revision != null ? `，当前 v${p.existing_revision}` : ""} · {p.package_level} 级 · {p.scope === "package" ? "正式测试" : p.scope === "samples" ? "公开样例" : "评测不可用"} {p.cases} 个</p><p className="muted">来源：{p.source} · 审核人：{p.reviewer || "样例级题目"}</p><p className="muted">许可：{p.license_note}</p><p>{p.notice}</p></article>)}
      <div className="admin-actions"><button className="btn-primary" disabled={disabled || !rights} onClick={() => void run(async () => { await api.catalogConfirm(preview.preview_id); setPreview(undefined); setSelected(undefined); setNotice("导入完成。请核对列表中的上下架状态；相同内容未追加版本。"); await load(0); })}>确认导入 {preview.rows.length} 道题</button><button className="ghost" disabled={disabled} onClick={() => setPreview(undefined)}>取消本次导入</button></div>
    </section>}
  </div>;
}
