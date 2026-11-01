import { useState } from "react";
import { api, messageOf, type EvidenceItem, type TestSummary } from "../api";
import { resultState } from "../result-state";
import { executionLabel } from "../evidence-state";

export function VerdictBadge({ verdict, tests }: { verdict: string | null; tests?: TestSummary | null }) {
  const state = resultState(verdict, tests);
  return <span className={"badge " + state.tone}>{state.label}</span>;
}

export function ErrorNotice({ message, onRetry }: { message: string; onRetry?: () => void }) {
  if (!message) return null;
  return <div className="err" role="alert"><span>{message}</span>{onRetry && <button className="ghost" onClick={onRetry}>重试</button>}</div>;
}

export function CardDownload({ id }: { id: number }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function download() {
    if (busy) return;
    setBusy(true);
    setError("");
    try { await api.downloadCard(id); } catch (e) { setError(messageOf(e)); } finally { setBusy(false); }
  }
  return <div className="download-control"><button className="ghost" onClick={download} disabled={busy}>{busy ? "导出中…" : "导出记录"}</button>{error && <span className="error-text" role="alert">{error}</span>}</div>;
}

export function EvidencePanel({ items, loading, error, onRetry }: { items: EvidenceItem[]; loading: boolean; error: string; onRetry: () => void }) {
  return <div className="evidence-panel"><h3>执行证据</h3>
    {loading && <p className="muted" role="status">正在加载编译与运行记录…</p>}
    <ErrorNotice message={error} onRetry={onRetry} />
    {!loading && !error && !items.length && <p className="muted">尚无可展示的执行记录，不能据此确认错误原因。</p>}
    {items.map((item) => {
      const payload = item.payload;
      const fields: [string, string][] = [["stdin", "输入"], ["input", "输入"], ["expected", "预期输出"], ["stdout", "实际输出"], ["stderr", item.type === "compile_log" ? "编译器信息" : "错误输出"]];
      const presentFields = fields.filter(([key]) => typeof payload[key] === "string" && (payload[key] !== "" || key === "stdout"));
      const escapedPaths = Array.isArray(payload.escaped_fields) ? payload.escaped_fields.filter((path): path is string => typeof path === "string" && path.startsWith("/")) : [];
      const escapedLabels = [...new Set(escapedPaths.map((path) => fields.find(([key]) => path === "/" + key)?.[1] ?? path))];
      const escapedInput = escapedPaths.some((path) => path === "/input" || path.startsWith("/input/") || path === "/stdin" || path.startsWith("/stdin/"));
      const truncatedInput = payload.input_truncated === true;
      return <details key={item.id} className="evidence-item" open={item.type === "compile_log" && payload.ok === false}>
        <summary>{item.type === "compile_log" ? (payload.ok === true ? "编译成功" : "编译器记录") : item.type === "run_trace" ? "测试 " + String(payload.case ?? "") + " · " + executionLabel(payload) : "检查记录"}<span className="dim"> · 证据 #{item.id}</span></summary>
        {escapedLabels.length > 0 && <p className="scope-note">以下文本含不可见字符，已用 <code>{"\\u0000"}</code> 转义显示：{escapedLabels.join("、")}。判题结果仍以原始输出为准。</p>}
        {presentFields.map(([key, label]) => <div key={key}><h4>{label}{payload[(key === "stdin" ? "input" : key) + "_truncated"] === true ? "（仅显示部分）" : ""}</h4><pre>{String(payload[key]) || "（无输出）"}</pre></div>)}
        {!presentFields.length && <pre>{JSON.stringify(payload, null, 2)}</pre>}
        {(item.reproducible === false || truncatedInput || escapedInput) && <p className="scope-note">{escapedInput ? "输入含已转义的不可见字符" + (truncatedInput ? "，且仅保存了部分内容" : "") + "，展示文本不能直接作为原始输入运行" : truncatedInput ? "保存的输入不完整，不能直接用这份记录复现" : "这条记录的输入无法直接用于复现"}；请联系教师获取原始测试数据。</p>}
        {typeof payload.time_ms === "number" && payload.time_ms > 0 && <p className="dim">运行用时 {payload.time_ms} ms</p>}
      </details>;
    })}
  </div>;
}
