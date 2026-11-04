import type { SessionHistory, SessionReply } from "./api";

export function shouldVerifySubmission(status: string, originId: number, candidateId: number, alreadyVerified: boolean): boolean {
  return ["open", "unverified"].includes(status) && candidateId !== originId && !alreadyVerified;
}

export function sameSource(code: string, language: string, previousCode: string, previousLanguage: string): boolean {
  return language === previousLanguage && code.replace(/\r\n/g, "\n") === previousCode.replace(/\r\n/g, "\n");
}

export function verificationRecovery(error: unknown): "retry" | "edit" | "restart" {
  const issue = error as { status?: number; code?: string; message?: string } | null;
  if (issue?.code === "verification_unchanged" || issue?.code === "verification_not_new"
    || issue?.message === "请先修改代码并重新提交，再核验新提交") return "edit";
  if (issue?.code === "verification_pending" || issue?.message === "新提交尚未完成判题") return "retry";
  if (issue?.status && [400, 403, 404, 409, 422].includes(issue.status)) return "restart";
  return "retry";
}

export async function verifyWithRefresh(options: {
  verify: () => Promise<SessionReply>;
  refresh: () => Promise<SessionHistory>;
}): Promise<{ reply?: SessionReply; history?: SessionHistory; error?: unknown }> {
  try { return { reply: await options.verify() }; }
  catch (error) {
    // 核验被拒绝时，旧的 resolved 不能继续充当当前状态证据。
    try { return { error, history: await options.refresh() }; }
    catch { return { error }; }
  }
}
