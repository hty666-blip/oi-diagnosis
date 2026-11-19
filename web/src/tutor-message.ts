import type { SessionHistory, SessionReply } from "./api";

export type ChatMessage = { role: "tutor" | "student"; level: number; text: string };

export async function sendMessageWithHistory(options: {
  send: () => Promise<SessionReply>;
  refresh: () => Promise<SessionHistory>;
  isCurrent: () => boolean;
}): Promise<{
  session: SessionReply;
  messages: ChatMessage[];
  accepted: boolean;
  notice: string;
} | null> {
  if (!options.isCurrent()) return null;
  const reply = await options.send();
  if (!options.isCurrent()) return null;
  // 回执只确认本请求；历史可能已有其他页面的提问或新的核验结果。
  let history: SessionHistory;
  try { history = await options.refresh(); }
  catch {
    if (!options.isCurrent()) return null;
    throw new Error(reply.accepted === false || reply.action === "closed"
      ? "会话状态同步失败，当前问题已保留，请稍后重试。"
      : "消息可能已保存，但会话记录同步失败。当前问题已保留，请再次提问以同步已有回复。");
  }
  if (!options.isCurrent()) return null;
  if (history.session_id !== reply.session_id) {
    throw new Error("会话记录与本次提问不一致，当前问题已保留，请重新打开记录。");
  }
  const accepted = reply.accepted ?? reply.action !== "closed";
  return {
    session: { session_id: history.session_id, status: history.status, level: history.level,
      verified_submission_id: history.verified_submission_id, verdict: history.verdict,
      reply: "", action: "hint" },
    messages: history.turns.map((turn) => ({ role: turn.role, level: turn.level, text: turn.content })),
    accepted,
    notice: accepted ? "" : reply.reply,
  };
}
