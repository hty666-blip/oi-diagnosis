import type { SessionHistory, SessionReply } from "./api";

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
