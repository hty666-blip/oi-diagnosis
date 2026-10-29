import assert from "node:assert/strict";
import { test } from "node:test";
import { sourceModule } from "./load-source.mjs";

const { submissionPhase, taskLabel, pendingKey, parsePendingId, pollDelay, pollSubmission, cancelAndRefresh } = await sourceModule("../src/judge-task.ts");
const record = (stage, verdict = null, job) => ({ id: 42, stage, verdict, tests_passed: null, job });
const ready = record("READY", "WA", { status: "succeeded" });

function options(extra = {}) {
  let clock = 0;
  return { id: 42, signal: new AbortController().signal, onUpdate: () => {},
    now: () => clock, wait: async (ms) => { clock += ms; }, ...extra };
}

test("接受和运行中的任务不是判定完成；失败状态优先，旧同步终态保持兼容", () => {
  assert.equal(submissionPhase(record("QUEUED", null, { status: "queued" })), "pending");
  assert.equal(submissionPhase(record("READY", "AC", { status: "running" })), "pending");
  assert.equal(submissionPhase(record("READY", "AC", { status: "failed" })), "failed");
  assert.equal(submissionPhase(record("READY", "SAMPLE_PASS")), "complete");
  assert.equal(submissionPhase(record("INTERRUPTED")), "failed");
  assert.equal(taskLabel(record("COMPILING")), "正在编译代码");
  assert.equal(taskLabel(record("QUEUED", null, { last_error: "执行失败" })), "正在等待自动重试");
});

test("恢复记录按账号题号隔离，并拒绝无效或不安全的提交编号", () => {
  assert.notEqual(pendingKey(1, "PTV-0001"), pendingKey(2, "PTV-0001"));
  assert.notEqual(pendingKey(1, "PTV-0001"), pendingKey(1, "PTV-0002"));
  for (const raw of [null, "", "0", "-1", "1.5", "1e3", "Infinity", "9007199254740992"]) assert.equal(parsePendingId(raw), null);
  assert.equal(parsePendingId("42"), 42);
});

test("轮询按实际状态更新且在终态停止，退避不会持续加速请求", async () => {
  const rows = [record("QUEUED"), record("RUNNING"), ready];
  const updates = [];
  let calls = 0;
  const result = await pollSubmission(options({ fetchRecord: async () => rows[calls++], onUpdate: (value) => updates.push(value.stage) }));
  assert.equal(result.reason, "complete");
  assert.equal(calls, 3);
  assert.deepEqual(updates, ["QUEUED", "RUNNING", "READY"]);
  assert.deepEqual([1, 2, 3, 4, 100].map(pollDelay), [500, 1000, 2000, 4000, 4000]);
});

test("自动查询次数有界，暂停不触发第二次提交或伪造失败判定", async () => {
  let calls = 0;
  const result = await pollSubmission(options({ maxPolls: 2, fetchRecord: async () => { calls += 1; return record("QUEUED"); } }));
  assert.equal(calls, 2);
  assert.equal(result.reason, "paused");
  assert.equal(result.record.stage, "QUEUED");
  let failures = 0;
  const offline = await pollSubmission(options({ fetchRecord: async () => { failures += 1; throw new Error("offline"); } }));
  assert.equal(failures, 3);
  assert.equal(offline.reason, "paused");
});

test("切题或退出会中止查询；即使旧响应最后到达也不能更新新页面", async () => {
  const controller = new AbortController();
  let complete;
  let updates = 0;
  const pending = pollSubmission(options({ signal: controller.signal,
    fetchRecord: () => new Promise((resolve) => { complete = resolve; }), onUpdate: () => updates++ }));
  controller.abort();
  assert.equal((await pending).reason, "cancelled");
  complete(ready);
  await Promise.resolve();
  assert.equal(updates, 0);

  let current = true;
  const stale = await pollSubmission(options({ isCurrent: () => current,
    fetchRecord: async () => { current = false; return ready; }, onUpdate: () => updates++ }));
  assert.equal(stale.reason, "cancelled");
  assert.equal(updates, 0);
});

test("隐藏页面等待可见后才查询，后台停留时间不消耗当前查询窗口", async () => {
  let visible = false;
  let calls = 0;
  let hiddenWaits = 0;
  let clock = 0;
  const result = await pollSubmission(options({
    now: () => clock, maxDurationMs: 1000, isVisible: () => visible,
    waitVisible: async () => { hiddenWaits++; clock += 600000; visible = true; },
    fetchRecord: async () => { assert.equal(visible, true); calls++; return ready; },
  }));
  assert.equal(hiddenWaits, 1);
  assert.equal(calls, 1);
  assert.equal(result.reason, "complete");
});

test("不可访问的任务立即停止查询，服务失败终态不会被伪造为代码运行错误", async () => {
  let calls = 0;
  const inaccessible = await pollSubmission(options({ fetchRecord: async () => {
    calls++; throw Object.assign(new Error("not found"), { status: 404 });
  } }));
  assert.equal(calls, 1);
  assert.equal(inaccessible.reason, "paused");
  assert.equal(inaccessible.error.status, 404);
  const failed = await pollSubmission(options({ fetchRecord: async () => record("SANDBOX_ERROR", null, { status: "failed" }) }));
  assert.equal(failed.reason, "failed");
  assert.equal(failed.record.verdict, null);
});

test("无响应的查询也有单次上限，暂停后保留服务器任务供继续查看", async () => {
  const signals = [];
  const result = await pollSubmission(options({ requestTimeoutMs: 1, fetchRecord: (_id, signal) => {
    signals.push(signal); return new Promise(() => {});
  } }));
  assert.equal(result.reason, "paused");
  assert.equal(signals.length, 3);
  assert.ok(signals.every((signal) => signal.aborted));
  assert.match(result.error.message, /超时/);
});

test("刷新恢复已取消记录后立即停止查询，不把残留判定当作通过", async () => {
  const cancelled = { ...record("CANCELLED", "AC", { status: "cancelled" }), code: "saved code", parent_submission_id: 21 };
  let calls = 0;
  const result = await pollSubmission(options({ fetchRecord: async () => { calls++; return cancelled; } }));
  assert.equal(calls, 1);
  assert.equal(result.reason, "task_cancelled");
  assert.equal(submissionPhase(cancelled), "cancelled");
  assert.equal(taskLabel(cancelled), "已取消");
  assert.equal(result.record.code, "saved code");
  assert.equal(result.record.parent_submission_id, 21);
});

test("取消后必须读取最新记录；完成竞争和409不能被报告为取消成功", async () => {
  const calls = [];
  const controller = new AbortController();
  const result = await cancelAndRefresh(42, {
    signal: controller.signal,
    cancel: async (id, signal) => { calls.push(["cancel", id, signal]); throw Object.assign(new Error("ended"), { status: 409 }); },
    fetchRecord: async (id, signal) => { calls.push(["get", id, signal]); return ready; },
  });
  assert.deepEqual(calls, [["cancel", 42, controller.signal], ["get", 42, controller.signal]]);
  assert.equal(result.confirmed, false);
  assert.equal(submissionPhase(result.record), "complete");
  assert.equal(result.record.verdict, "WA");
});

test("取消响应不确定时以服务器事实为准；核对失败时不伪造已取消", async () => {
  const cancelled = record("CANCELLED", null, { status: "cancelled" });
  const signal = new AbortController().signal;
  const recovered = await cancelAndRefresh(42, { signal,
    cancel: async () => { throw new Error("connection lost"); }, fetchRecord: async () => cancelled });
  assert.equal(recovered.confirmed, true);
  assert.equal(recovered.record, cancelled);
  await assert.rejects(cancelAndRefresh(42, { signal, cancel: async () => cancelled,
    fetchRecord: async () => { throw new Error("cannot confirm"); } }), /cannot confirm/);
  const stillRunning = await cancelAndRefresh(42, { signal, cancel: async () => cancelled,
    fetchRecord: async () => record("RUNNING", null, { status: "running" }) });
  assert.equal(stillRunning.confirmed, false);
});

test("取消核对遇到页面切换时不读取新页面也不返回旧结果", async () => {
  const controller = new AbortController();
  let reads = 0;
  await assert.rejects(cancelAndRefresh(42, { signal: controller.signal,
    cancel: async () => { controller.abort(); throw new DOMException("stopped", "AbortError"); },
    fetchRecord: async () => { reads++; return ready; } }), (error) => error.name === "AbortError");
  assert.equal(reads, 0);
  const nextController = new AbortController();
  await assert.rejects(cancelAndRefresh(42, { signal: nextController.signal,
    cancel: async () => record("CANCELLED"),
    fetchRecord: async () => { nextController.abort(); return ready; } }), (error) => error.name === "AbortError");
});
