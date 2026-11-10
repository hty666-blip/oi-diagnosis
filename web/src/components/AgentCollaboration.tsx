import type { AgentCollaboration as Collaboration } from "../api";

const names: Record<string, string> = { evidence: "证据分析 Agent", diagnosis: "错因诊断 Agent", critic: "独立审查 Agent" };
const statuses: Record<string, string> = { completed: "已完成", approve: "审查通过", revise: "要求修订", uncertain: "仍有疑问", unavailable: "本轮不可用" };

export default function AgentCollaboration({ value, reviewed = false }: { value?: Collaboration | null; reviewed?: boolean }) {
  if (!value || value.version !== "collaboration-v1" || !Array.isArray(value.steps)) return null;
  return <details className="agent-collaboration" open={value.mode !== "rules"}>
    <summary>多 Agent 协作记录 <span>{value.mode === "multi_agent" ? "AI 分工分析与独立审查已完成" : value.mode === "rules" ? "本次采用规则路径" : "部分协作未完成"}</span></summary>
    {value.rounds > 1 && <p className="muted">已按审查意见完成第 {value.rounds} 轮分析。</p>}
    <ol>{value.steps.filter((item) => item && names[item.agent]).map((item, index) => <li key={index}>
      <div className="agent-step-head"><strong>{names[item.agent]}</strong><span>{item.round > 1 ? `第 ${item.round} 轮 · ` : ""}{item.method === "model" ? "AI" : "规则"} · {statuses[item.status] ?? "待核对"}</span></div>
      <p className="agent-step-summary">{item.summary}</p>
      {Array.isArray(item.evidence_ids) && item.evidence_ids.length > 0 && <p className="muted">依据：{item.evidence_ids.map((id) => `证据 #${id}`).join("、")}</p>}
      {Array.isArray(item.feedback) && ["revise", "uncertain"].includes(item.status) && item.feedback.length > 0 && <ul>{item.feedback.map((text, i) => <li key={i}>{text}</li>)}</ul>}
    </li>)}</ol>
    <p className="scope-note">协作审查通过仅表示解释可供参考，代码修改仍需重新提交测试。{reviewed ? "这份自动分析为历史记录，当前结论以教师审定为准。" : value.human_review_required ? "错因尚未获教师确认，可交由教练复核。" : ""}</p>
  </details>;
}
