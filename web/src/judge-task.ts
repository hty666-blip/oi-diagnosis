import type { SubmissionResult } from "./api";

export type SubmissionPhase = "pending" | "complete" | "failed" | "cancelled";

export function submissionPhase(record: Pick<SubmissionResult, "stage" | "verdict" | "job">): SubmissionPhase {
  if (record.job?.status === "cancelled" || record.stage === "CANCELLED") return "cancelled";
  if (record.job?.status === "failed" || ["SANDBOX_ERROR", "INTERRUPTED", "FAILED"].includes(record.stage)) return "failed";
  if (record.job?.status === "queued" || record.job?.status === "running") return "pending";
  if (record.stage === "READY" && record.verdict !== null) return "complete";
  return "pending";
}

export function taskLabel(record: Pick<SubmissionResult, "stage" | "job">): string {
  if (record.job?.status === "cancelled" || record.stage === "CANCELLED") return "已取消";
  if (record.job?.status === "failed") return "判题未完成";
  if (record.stage === "COMPILING") return "正在编译代码";
  if (record.stage === "RUNNING") return "正在执行测试";
  return record.job?.last_error ? "正在等待自动重试" : "已接收，等待判题";
}

export function pendingKey(userId: number, problemPid: string): string {
  return "oi-pending-submission:" + userId + ":" + problemPid;
}

export function parsePendingId(raw: string | null): number | null {
  if (!raw || !/^[1-9]\d*$/.test(raw)) return null;
  const id = Number(raw);
  return Number.isSafeInteger(id) ? id : null;
}

function aborted(): DOMException { return new DOMException("状态查询已停止", "AbortError"); }

export function pollDelay(attempt: number): number {
  return Math.min(4000, 500 * 2 ** Math.min(Math.max(attempt - 1, 0), 3));
}

function delay(ms: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.reject(aborted());
  return new Promise((resolve, reject) => {
    const stop = () => { clearTimeout(timer); reject(aborted()); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", stop); resolve(); }, ms);
    signal.addEventListener("abort", stop, { once: true });
  });
}

function waitUntilVisible(signal: AbortSignal): Promise<void> {
  if (typeof document === "undefined" || document.visibilityState !== "hidden") return Promise.resolve();
  if (signal.aborted) return Promise.reject(aborted());
  return new Promise((resolve, reject) => {
    const cleanup = () => { document.removeEventListener("visibilitychange", visible); signal.removeEventListener("abort", stop); };
    const stop = () => { cleanup(); reject(aborted()); };
    const visible = () => { if (document.visibilityState !== "hidden") { cleanup(); resolve(); } };
    document.addEventListener("visibilitychange", visible);
    signal.addEventListener("abort", stop, { once: true });
  });
}

interface PollOptions {
  id: number;
  signal: AbortSignal;
  fetchRecord: (id: number, signal: AbortSignal) => Promise<SubmissionResult>;
  onUpdate: (record: SubmissionResult) => void;
  isCurrent?: () => boolean;
  isVisible?: () => boolean;
  waitVisible?: (signal: AbortSignal) => Promise<void>;
  wait?: (ms: number, signal: AbortSignal) => Promise<void>;
  now?: () => number;
  maxPolls?: number;
  maxDurationMs?: number;
  requestTimeoutMs?: number;
}

export interface PollResult { reason: "complete" | "failed" | "paused" | "cancelled" | "task_cancelled"; record?: SubmissionResult; error?: unknown }

export async function cancelAndRefresh(id: number, options: {
  signal: AbortSignal;
  cancel: (id: number, signal: AbortSignal) => Promise<SubmissionResult>;
  fetchRecord: (id: number, signal: AbortSignal) => Promise<SubmissionResult>;
}): Promise<{ record: SubmissionResult; confirmed: boolean; error?: unknown }> {
  let cancellationError: unknown;
  try { await options.cancel(id, options.signal); }
  catch (error) {
    if (options.signal.aborted) throw error;
    cancellationError = error;
  }
  // 取消和完成可能竞争，必须重新读取服务器结果，不能只凭点击反馈宣布取消。
  const record = await options.fetchRecord(id, options.signal);
  if (options.signal.aborted) throw aborted();
  return { record, confirmed: submissionPhase(record) === "cancelled", error: cancellationError };
}

async function boundedFetch(options: PollOptions): Promise<SubmissionResult> {
  const controller = new AbortController();
  const stop = () => controller.abort(aborted());
  options.signal.addEventListener("abort", stop, { once: true });
  if (options.signal.aborted) stop();
  const timer = setTimeout(() => controller.abort(new Error("状态查询超时，请稍后继续查看。")), options.requestTimeoutMs ?? 15000);
  let rejectAbort: (() => void) | undefined;
  const cancelled = new Promise<never>((_resolve, reject) => {
    rejectAbort = () => reject(controller.signal.reason ?? aborted());
    controller.signal.addEventListener("abort", rejectAbort, { once: true });
    if (controller.signal.aborted) rejectAbort();
  });
  try { return await Promise.race([options.fetchRecord(options.id, controller.signal), cancelled]); }
  finally {
    clearTimeout(timer);
    options.signal.removeEventListener("abort", stop);
    if (rejectAbort) controller.signal.removeEventListener("abort", rejectAbort);
  }
}

export async function pollSubmission(options: PollOptions): Promise<PollResult> {
  const { signal } = options;
  const wait = options.wait ?? delay;
  const now = options.now ?? Date.now;
  const isCurrent = () => !signal.aborted && (options.isCurrent?.() ?? true);
  const visible = options.isVisible ?? (() => typeof document === "undefined" || document.visibilityState !== "hidden");
  let startedAt = now();
  let failures = 0;
  let lastRecord: SubmissionResult | undefined;
  for (let attempt = 1; attempt <= (options.maxPolls ?? 45); attempt += 1) {
    if (!isCurrent()) return { reason: "cancelled" };
    try {
      if (!visible()) {
        const hiddenAt = now();
        await (options.waitVisible ?? waitUntilVisible)(signal);
        startedAt += now() - hiddenAt;
      }
      if (!isCurrent()) return { reason: "cancelled" };
      if (now() - startedAt > (options.maxDurationMs ?? 120000)) return { reason: "paused", record: lastRecord };
      const record = await boundedFetch(options);
      if (!isCurrent()) return { reason: "cancelled" };
      lastRecord = record;
      options.onUpdate(record);
      failures = 0;
      const phase = submissionPhase(record);
      if (phase !== "pending") return { reason: phase === "cancelled" ? "task_cancelled" : phase, record };
    } catch (error) {
      if (!isCurrent()) return { reason: "cancelled" };
      const status = (error as { status?: number } | null)?.status;
      if (status === 401 || status === 403 || status === 404) return { reason: "paused", record: lastRecord, error };
      failures += 1;
      if (failures >= 3) return { reason: "paused", record: lastRecord, error };
    }
    if (attempt === (options.maxPolls ?? 45)) break;
    try { await wait(pollDelay(attempt), signal); }
    catch { return { reason: "cancelled" }; }
  }
  return { reason: "paused", record: lastRecord };
}
