import { useEffect, useRef, useState } from "react";
import {
  api, ApiError, messageOf, type EvidenceItem, type ReviewDecision,
  type ReviewEntry, type ReviewHistory, type SubmissionResult,
} from "../api";
import { CardDownload, ErrorNotice, EvidencePanel, VerdictBadge } from "./RecordUI";
import { executionLabel } from "../evidence-state";

const DECISION_LABEL: Record<ReviewDecision, string> = {
  confirm: "确认诊断", revise: "修订诊断", return: "退回补充",
};

function ReviewEntries({ items }: { items: ReviewEntry[] }) {
  return <ol className="review-history">
    {items.map((entry) => <li key={entry.id}>
      <div className="row"><strong>{DECISION_LABEL[entry.decision]}</strong><span className="dim">版本 {entry.revision} · {entry.actor.name} · {new Date(entry.created_at).toLocaleString("zh-CN")}</span></div>
      <p className="narrative">{entry.narrative}</p>
      <p className="dim">{entry.evidence_ids.length ? "依据：" + entry.evidence_ids.map((id) => "证据 #" + id).join("、") : "未引用执行证据，仅请求补充材料。"}</p>
      <details className="audit-snapshot"><summary>查看变更前后记录</summary><div className="sample"><div><h4>变更前</h4><pre>{JSON.stringify(entry.before, null, 2)}</pre></div><div><h4>变更后</h4><pre>{JSON.stringify(entry.after, null, 2)}</pre></div></div></details>
    </li>)}
  </ol>;
}

export function ReviewHistoryPanel({ submissionId, pending = false }: { submissionId: number; pending?: boolean }) {
  const [history, setHistory] = useState<ReviewHistory | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true); setError(""); setHistory(null);
    api.reviews(submissionId).then((value) => { if (active) setHistory(value); })
      .catch((e) => { if (active) setError(messageOf(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [submissionId, retry]);
  const latest = history?.items[(history?.items.length ?? 0) - 1];
  return <section className="student-review" aria-label="教师复核">
    <div className="card-head"><h3>教师复核</h3><button className="ghost" onClick={() => setRetry((value) => value + 1)} disabled={loading}>刷新复核状态</button></div>
    {loading && <p className="muted" role="status">正在核对复核记录…</p>}
    <ErrorNotice message={error} onRetry={() => setRetry((value) => value + 1)} />
    {history && !history.items.length && <p className="muted">{pending ? "当前诊断证据不足，等待教师复核。在此之前，自动诊断只供进一步检查。" : "暂无教师审定记录。自动诊断不等同于教师结论。"}</p>}
    {latest && <>
      <p className="review-outcome" role="status">{latest.actor.name}{latest.decision === "return" ? "已请求补充材料，问题仍待核对。" : latest.decision === "revise" ? "已修订诊断说明。" : "已确认诊断说明。"}人工复核不改变程序的执行判定。</p>
      <ReviewEntries items={history?.items ?? []} />
    </>}
  </section>;
}

export default function ReviewPanel({ submissionId, onReviewed }: { submissionId: number; onReviewed: () => void }) {
  const [submission, setSubmission] = useState<SubmissionResult | null>(null);
  const [evidence, setEvidence] = useState<EvidenceItem[]>([]);
  const [history, setHistory] = useState<ReviewHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [decision, setDecision] = useState<ReviewDecision>("confirm");
  const [narrative, setNarrative] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [saving, setSaving] = useState(false);
  const [mustRefresh, setMustRefresh] = useState(false);
  const [notice, setNotice] = useState("");
  const generation = useRef(0);
  const saveLock = useRef(false);

  async function load() {
    const request = ++generation.current;
    setLoading(true); setError("");
    try {
      const [record, records, reviews] = await Promise.all([
        api.getSubmission(submissionId), api.evidence(submissionId), api.reviews(submissionId),
      ]);
      if (request !== generation.current) return;
      setSubmission(record); setEvidence(records); setHistory(reviews);
      setSelected((ids) => ids.filter((id) => records.some((item) => item.id === id)));
      setMustRefresh(false);
    } catch (e) { if (request === generation.current) setError(messageOf(e)); }
    finally { if (request === generation.current) setLoading(false); }
  }

  useEffect(() => {
    void load();
    return () => { generation.current += 1; };
  }, [submissionId]);

  async function save() {
    if (!history || loading || saveLock.current || mustRefresh || narrative.trim().length < 6 || (decision !== "return" && !selected.length)) return;
    saveLock.current = true; setSaving(true); setError(""); setNotice("");
    try {
      const result = await api.review({
        submission_id: submissionId, expected_revision: history.current_revision,
        decision, narrative: narrative.trim(), evidence_ids: selected,
      });
      setNotice(DECISION_LABEL[result.review.decision] + "已保存为版本 " + result.review.revision + "，审计历史已保留。");
      setMustRefresh(true); setNarrative(""); setSelected([]);
      await load();
      onReviewed();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setMustRefresh(true);
        setError("这条诊断已被更新，你的填写内容已保留。请刷新记录，核对新的审定版本后再提交。");
      } else setError(messageOf(e));
    } finally { setSaving(false); saveLock.current = false; }
  }

  return <section className="card review-panel" aria-label={"复核提交 " + submissionId}>
    <div className="card-head"><h2>复核提交 #{submissionId}</h2><button className="ghost" onClick={() => void load()} disabled={loading || saving}>刷新记录</button></div>
    {notice && <p className="review-outcome" role="status">{notice}</p>}
    <ErrorNotice message={error} onRetry={() => void load()} />
    {loading && <p role="status">正在读取学生源码、执行证据和审计版本…</p>}
    {submission && <><div className="row"><strong>{submission.problem_pid} {submission.problem_title}</strong><VerdictBadge verdict={submission.verdict} tests={submission.tests_passed} /><CardDownload id={submission.id} /></div>
      <p className="muted">教师只审定诊断说明，机器判题结果始终保留。</p>
      <div className="review-layout">
        <section aria-label="学生源码与证据"><h3>学生提交的代码</h3><pre className="review-code"><code>{submission.code}</code></pre>
          <EvidencePanel items={evidence} loading={loading} error="" onRetry={() => void load()} />
        </section>
        <section aria-label="诊断审定">
          <h3>当前诊断说明</h3><p className="narrative">{submission.diagnosis?.narrative || "暂无自动诊断说明。"}</p>
          {history && <p className="dim">当前审定版本 {history.current_revision}</p>}
          <form className="review-form" onSubmit={(e) => { e.preventDefault(); void save(); }}>
            <label htmlFor="review-decision">处理方式</label>
            <select id="review-decision" value={decision} onChange={(e) => setDecision(e.target.value as ReviewDecision)} disabled={saving}>
              <option value="confirm">确认：依据证据认可诊断</option><option value="revise">修订：替换诊断说明</option><option value="return">退回：要求补充材料</option>
            </select>
            <fieldset disabled={loading || saving}><legend>引用本次提交的证据{decision === "return" ? "（可选）" : "（至少一条）"}</legend>
              {!evidence.length && <p className="muted">没有执行证据，只能退回并说明需补充的材料。</p>}
              {evidence.map((item) => <label className="evidence-choice" key={item.id}><input type="checkbox" checked={selected.includes(item.id)} onChange={(e) => setSelected((ids) => e.target.checked ? [...ids, item.id] : ids.filter((id) => id !== item.id))} />证据 #{item.id} · {item.type === "compile_log" ? "编译器记录" : item.type === "run_trace" ? "测试 " + String(item.payload.case ?? "") + " · " + executionLabel(item.payload) : "检查记录"}</label>)}
            </fieldset>
            <label htmlFor="review-narrative">{decision === "revise" ? "修订后的诊断说明与依据" : decision === "return" ? "需要学生补充什么" : "确认说明与依据"}</label>
            <textarea id="review-narrative" rows={5} minLength={6} maxLength={4000} required value={narrative} onChange={(e) => setNarrative(e.target.value)} disabled={saving} placeholder={decision === "return" ? "说明还缺哪些信息，以及学生下一步应如何补充。" : "说明观察到的事实、引用的证据及建议检查的代码。至少 6 个字。"} />
            <p className="dim">每次提交会新增审计记录，保存操作者与变更前后内容。</p>
            <button type="submit" className="primary" disabled={loading || saving || !history || mustRefresh || narrative.trim().length < 6 || (decision !== "return" && !selected.length)}>{saving ? "正在保存审定…" : "保存" + DECISION_LABEL[decision]}</button>
          </form>
        </section>
      </div>
      <section className="review-audit" aria-label="审定历史"><h3>审定历史</h3>{history?.items.length ? <ReviewEntries items={history.items} /> : <p className="muted">尚无人工审定。首次提交也会完整记录变更前后的内容。</p>}</section>
    </>}
  </section>;
}
