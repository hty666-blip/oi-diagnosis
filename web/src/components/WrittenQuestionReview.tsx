import { useEffect, useRef, useState } from "react";
import { api, messageOf, type WrittenDetail, type WrittenDraft, type WrittenReviewInput } from "../api";
import { createWrittenReviewIntent } from "../written-review";
import { ErrorNotice } from "./RecordUI";

const issueNames: Record<string, string> = { answer_conflict: "答案冲突", withdrawn_question_requires_review: "撤题线索", possibly_out_of_syllabus: "大纲范围待核对", code_in_image: "程序保存在图片中" };
const statuses = { needs_review: "待审核", approved: "审核通过", withdrawn: "已撤下" };

export default function WrittenQuestionReview({ batchId, detail, onSaved, onBusy }: {
  batchId: string; detail: WrittenDetail; onSaved: (next: WrittenDetail) => void; onBusy: (busy: boolean) => void;
}) {
  const review = detail.review!;
  const [draft, setDraft] = useState<WrittenDraft>(structuredClone(review.draft));
  const [resolutions, setResolutions] = useState<Record<string, string>>({ ...review.resolutions });
  const [note, setNote] = useState(""), [rights, setRights] = useState(false);
  const [error, setError] = useState(""), [notice, setNotice] = useState(""), [busy, setBusy] = useState(false);
  const intent = useRef(createWrittenReviewIntent()), lock = useRef(false), alive = useRef(true);
  const feedback = useRef<HTMLDivElement>(null);
  useEffect(() => { alive.current = true; return () => { alive.current = false; onBusy(false); }; }, []);
  useEffect(() => { setDraft(structuredClone(review.draft)); setResolutions({ ...review.resolutions }); setNote(""); setRights(false); }, [review.revision]);
  useEffect(() => { if (error || notice) feedback.current?.focus(); }, [error, notice]);
  const leaves = detail.knowledge_points.filter((n) => n.is_leaf);
  const critical = review.source_flags.filter((f) => issueNames[f]);
  const choices = Array.isArray(draft.answer.option_ids) ? draft.answer.option_ids as string[] : [];
  const isChoice = draft.question_type === "single_choice" || draft.question_type === "multiple_choice";
  const blocked = busy || review.status === "withdrawn";
  function change<K extends keyof WrittenDraft>(key: K, value: WrittenDraft[K]) { setDraft((d) => ({ ...d, [key]: value })); setError(""); }
  function choose(id: string, checked: boolean) {
    change("answer", { kind: draft.question_type === "single_choice" ? "option" : "options", option_ids: draft.question_type === "single_choice" ? [id] : checked ? [...choices, id] : choices.filter((v) => v !== id) });
  }
  function changeType(value: WrittenDraft["question_type"]) {
    const choice = value === "single_choice" || value === "multiple_choice";
    setDraft((d) => ({ ...d, question_type: value, options: choice ? (isChoice ? d.options : ["A", "B", "C", "D"].map((id) => ({ id, text: "" }))) : [],
      answer: value === "fill_blank" ? { kind: "text", accepted: [], normalization: "exact" } : value === "true_false" ? { kind: "boolean" } : { kind: value === "single_choice" ? "option" : "options", option_ids: [] }, scoring_rule: "unconfirmed" }));
  }
  async function submit(action: WrittenReviewInput["action"]) {
    if (lock.current) return;
    lock.current = true; setBusy(true); onBusy(true); setError(""); setNotice("");
    const body = intent.current({ expected_revision: review.revision, action, note, ...(action === "save" || action === "approve" ? { draft } : {}), rights_confirmed: action === "approve" && rights, resolutions });
    try {
      const result = await api.writtenReview(batchId, detail.record.id, body);
      if (!alive.current) return;
      onSaved(result); setNotice(result.applied_revision !== result.review?.revision ? `该操作已保存为修订 v${result.applied_revision}；当前已有更新，现显示最新状态。` : action === "approve" ? "已审核通过并发布独立版本。" : action === "save" ? "修订稿已保存，当前题已下架待审核。" : action === "withdraw" ? "题目已撤下，历史版本保留。" : "已恢复待审核状态。");
    } catch (e) { if (alive.current) setError(messageOf(e)); }
    finally { lock.current = false; if (alive.current) { setBusy(false); onBusy(false); } }
  }
  async function togglePublication() {
    const pub = review.publication;
    if (!pub || lock.current) return;
    lock.current = true; setBusy(true); onBusy(true); setError("");
    try { await api.writtenPublicationState(pub.id, pub.catalog_version, !pub.is_active); if (alive.current) onSaved(await api.writtenQuestion(batchId, detail.record.id)); }
    catch (e) { if (alive.current) setError(messageOf(e)); }
    finally { lock.current = false; if (alive.current) { setBusy(false); onBusy(false); } }
  }
  return <section className="written-review">
    <p>修订 v{review.revision} · {statuses[review.status]}{review.publication ? ` · 发布版 v${review.publication.revision} ${review.publication.is_active ? "已上架" : "已下架"}` : ""}</p>
    {(error || notice) && <div ref={feedback} tabIndex={-1}>{error && <ErrorNotice message={error} />}{notice && <p role="status">{notice}</p>}</div>}
    <fieldset disabled={blocked} className="written-review-fields"><legend>修订稿</legend>
      <div className="written-review-row"><label>题型<select value={draft.question_type} onChange={(e) => changeType(e.target.value as WrittenDraft["question_type"])}><option value="single_choice">单选</option><option value="multiple_choice">多选</option><option value="true_false">判断</option><option value="fill_blank">填空</option></select></label>
        <label>级别<select value={draft.level ?? ""} onChange={(e) => change("level", (e.target.value || null) as WrittenDraft["level"])}><option value="">请选择</option><option value="J">CSP-J 入门级</option><option value="S">CSP-S 提高级</option></select></label>
        <label>分值<input type="number" min="0" max="100" step="any" value={draft.score} onChange={(e) => change("score", e.target.value === "" ? 0 : Number(e.target.value))} /></label></div>
      <label>题干<textarea rows={4} value={draft.stem} maxLength={30000} onChange={(e) => change("stem", e.target.value)} /></label>
      <details><summary>编辑共用材料 / 程序</summary><label>共用材料<textarea rows={12} spellCheck={false} value={draft.shared_stem} maxLength={60000} onChange={(e) => change("shared_stem", e.target.value)} /></label></details>
      {isChoice && <section><h3>选项与正确答案</h3>{draft.options.map((o, i) => <div className="written-option-edit" key={o.id}><label className="catalog-check"><input type={draft.question_type === "single_choice" ? "radio" : "checkbox"} name={`answer-${detail.record.id}`} checked={choices.includes(o.id)} onChange={(e) => choose(o.id, e.target.checked)} />{o.id}</label><input aria-label={`选项 ${o.id}`} value={o.text} maxLength={4000} onChange={(e) => change("options", draft.options.map((v, n) => n === i ? { ...v, text: e.target.value } : v))} /><button type="button" className="ghost" onClick={() => { change("options", draft.options.filter((_, n) => n !== i)); change("answer", { ...draft.answer, option_ids: choices.filter((id) => id !== o.id) }); }}>移除</button></div>)}<button className="ghost" type="button" disabled={draft.options.length >= 26} onClick={() => { const id = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("").find((s) => !draft.options.some((o) => o.id === s)); if (id) change("options", [...draft.options, { id, text: "" }]); }}>添加选项</button></section>}
      {draft.question_type === "true_false" && <label>正确答案<select value={typeof draft.answer.value === "boolean" ? String(draft.answer.value) : ""} onChange={(e) => change("answer", e.target.value === "" ? { kind: "boolean" } : { kind: "boolean", value: e.target.value === "true" })}><option value="">请选择</option><option value="true">正确</option><option value="false">错误</option></select></label>}
      {draft.question_type === "fill_blank" && <><label>可接受答案（每行一个）<textarea rows={3} value={Array.isArray(draft.answer.accepted) ? draft.answer.accepted.join("\n") : ""} onChange={(e) => change("answer", { ...draft.answer, kind: "text", accepted: e.target.value.split("\n") })} /></label><label>空白比较规则<select value={String(draft.answer.normalization ?? "exact")} onChange={(e) => change("answer", { ...draft.answer, normalization: e.target.value })}><option value="exact">精确匹配，保留空格</option><option value="trim_outer_whitespace">仅忽略首尾空白</option></select></label></>}
      <label>计分规则<select value={draft.scoring_rule} onChange={(e) => change("scoring_rule", e.target.value as WrittenDraft["scoring_rule"])}><option value="unconfirmed">待确认</option>{draft.question_type === "fill_blank" ? <option value="exact_text">按指定文本规则，全对得分</option> : <option value="all_or_nothing">全对得分，错选或漏选不得分</option>}</select></label>
      <div className="written-knowledge-row"><label>主知识点<select value={draft.primary_knowledge_point_id ?? ""} onChange={(e) => { const id = e.target.value || null; setDraft((d) => ({ ...d, primary_knowledge_point_id: id, secondary_knowledge_point_ids: d.secondary_knowledge_point_ids.filter((v) => v !== id) })); setError(""); }}><option value="">请选择</option>{leaves.map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}</select></label><label>次知识点（按 Ctrl 多选）<select multiple size={6} value={draft.secondary_knowledge_point_ids} onChange={(e) => change("secondary_knowledge_point_ids", [...e.target.selectedOptions].map((o) => o.value))}>{leaves.filter((n) => n.id !== draft.primary_knowledge_point_id).map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}</select></label></div>
      {!!detail.normalized.knowledge_suggestions.length && <div className="admin-actions">{detail.normalized.knowledge_suggestions.map((n) => <button className="ghost" type="button" key={n.id} onClick={() => { change("primary_knowledge_point_id", n.id); change("secondary_knowledge_point_ids", draft.secondary_knowledge_point_ids.filter((v) => v !== n.id)); }}>填入建议：{n.name}</button>)}</div>}
      <label>解析<textarea rows={4} value={draft.explanation} maxLength={12000} onChange={(e) => change("explanation", e.target.value)} /></label>
      <label>答案核对依据<textarea rows={2} value={draft.answer_reference} maxLength={2000} placeholder="原卷、答案页的位置，或人工推导依据" onChange={(e) => change("answer_reference", e.target.value)} /></label>
      {critical.map((flag) => <label key={flag}>{issueNames[flag]}：处理依据<textarea rows={2} maxLength={2000} value={resolutions[flag] ?? ""} onChange={(e) => setResolutions((s) => ({ ...s, [flag]: e.target.value }))} /></label>)}
      <label className="catalog-check"><input type="checkbox" checked={rights} onChange={(e) => setRights(e.target.checked)} />确认本题题面、图片和解析可在本系统用于教学</label>
    </fieldset>
    <label className="admin-form">本次操作说明<textarea rows={2} maxLength={2000} value={note} disabled={busy} onChange={(e) => setNote(e.target.value)} /></label>
    <p className="muted">保存修订会将当前题下架；审核通过后生成新发布版，原始记录和历史版本继续保留。</p>
    <div className="admin-actions"><button className="ghost" disabled={blocked || note.trim().length < 3} onClick={() => void submit("save")}>保存修订稿</button><button className="btn-primary" disabled={blocked || !rights || note.trim().length < 3} onClick={() => void submit("approve")}>审核通过并发布</button>{review.status === "withdrawn" ? <button className="ghost" disabled={busy || note.trim().length < 3} onClick={() => void submit("reopen")}>恢复待审核</button> : <button className="ghost" disabled={busy || note.trim().length < 3} onClick={() => void submit("withdraw")}>撤下此题</button>}{review.status === "approved" && review.publication && <button className="ghost" disabled={busy} onClick={() => void togglePublication()}>{review.publication.is_active ? "下架发布版" : "重新上架发布版"}</button>}</div>
  </section>;
}

export function WrittenReviewHistory({ batchId, questionId }: { batchId: string; questionId: string }) {
  const [items, setItems] = useState<Awaited<ReturnType<typeof api.writtenReviewHistory>>["items"]>([]);
  const [more, setMore] = useState(false), [error, setError] = useState("");
  const [snapshot, setSnapshot] = useState<Awaited<ReturnType<typeof api.writtenHistoricalReview>>>();
  const [busy, setBusy] = useState(false);
  const controller = useRef<AbortController>();
  const actions: Record<string, string> = { save: "保存修订", approve: "审核发布", withdraw: "撤下", reopen: "恢复待审核" };
  async function load(before?: number) {
    controller.current?.abort(); const current = new AbortController(); controller.current = current; setBusy(true); setError("");
    try { const value = await api.writtenReviewHistory(batchId, questionId, before, current.signal); setItems(value.items); setMore(value.has_more); }
    catch (e) { if (!current.signal.aborted) setError(messageOf(e)); }
    finally { if (!current.signal.aborted) setBusy(false); }
  }
  useEffect(() => { void load(); return () => controller.current?.abort(); }, [batchId, questionId]);
  async function open(revision: number) {
    controller.current?.abort(); const current = new AbortController(); controller.current = current; setBusy(true); setError("");
    try { setSnapshot(await api.writtenHistoricalReview(batchId, questionId, revision, current.signal)); }
    catch (e) { if (!current.signal.aborted) setError(messageOf(e)); }
    finally { if (!current.signal.aborted) setBusy(false); }
  }
  return <section>{error && <ErrorNotice message={error} />}{busy && <p role="status">正在读取修订记录…</p>}<div className="table-scroll"><table><thead><tr><th>修订</th><th>操作 / 管理员</th><th>说明</th><th>时间</th><th>内容</th></tr></thead><tbody>{items.map((r) => <tr key={r.revision}><td>v{r.revision}</td><td>{actions[r.action] ?? r.action} · #{r.actor_id}</td><td>{r.note}</td><td>{new Date(r.created_at).toLocaleString("zh-CN")}</td><td><button className="ghost" disabled={busy} onClick={() => void open(r.revision)}>查看</button></td></tr>)}</tbody></table></div>{!busy && !items.length && <p>尚无人工修订记录。</p>}<div className="admin-actions"><button className="ghost" disabled={busy} onClick={() => void load()}>最新记录</button><button className="ghost" disabled={busy || !more} onClick={() => void load(items[items.length - 1]?.revision)}>更早记录</button></div>{snapshot && <section className="admin-section"><h3>修订 v{snapshot.revision}（只读）</h3><p>{snapshot.note}</p><p>{snapshot.snapshot.draft.stem}</p><pre className="written-raw">{JSON.stringify(snapshot.snapshot.draft, null, 2)}</pre></section>}</section>;
}
