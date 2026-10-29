import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { sourceModule } from "./load-source.mjs";

const { api, ApiError } = await sourceModule("../src/api.ts");
let storage;
let events;
const originalFetch = globalThis.fetch;

beforeEach(() => {
  storage = new Map([["oi_token", "local-test-token"], ["oi_user", "{}"], ["oi-draft:1:PTV-0001", "unsaved code"]]);
  events = [];
  globalThis.localStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  };
  globalThis.window = { dispatchEvent: (event) => { events.push(event.type); return true; } };
});
afterEach(() => {
  globalThis.fetch = originalFetch;
  delete globalThis.localStorage;
  delete globalThis.window;
});

const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});

test("错误密码不会触发已登录会话过期；401 受保护请求会清凭证并保留草稿", async () => {
  globalThis.fetch = async () => json({ detail: "unauthorized" }, 401);
  await assert.rejects(api.login("student01", "wrong"), (error) =>
    error instanceof ApiError && error.status === 401 && error.message.includes("密码"));
  assert.equal(storage.get("oi_token"), "local-test-token");
  assert.deepEqual(events, []);
  await assert.rejects(api.getSubmission(42), (error) => error.status === 401);
  assert.equal(storage.has("oi_token"), false);
  assert.equal(storage.get("oi-draft:1:PTV-0001"), "unsaved code");
  assert.deepEqual(events, ["oi-session-expired"]);
});

test("教师复核发送当前服务器版本和所选证据，409 保持可识别，避免静默覆盖", async () => {
  let captured;
  globalThis.fetch = async (url, options) => {
    captured = { url, options };
    return json({ detail: "诊断版本已更新" }, 409);
  };
  const review = { submission_id: 42, expected_revision: 3, decision: "revise",
    narrative: "根据失败输入修订检查方向。", evidence_ids: [9, 12] };
  await assert.rejects(api.review(review), (error) =>
    error instanceof ApiError && error.status === 409 && error.message === "诊断版本已更新");
  assert.equal(captured.url, "/api/v1/coach/reviews");
  assert.equal(captured.options.headers.Authorization, "Bearer local-test-token");
  assert.deepEqual(JSON.parse(captured.options.body), review);
  assert.equal(storage.get("oi_token"), "local-test-token");
});

test("重新提交显式携带父记录，首次提交不伪造父记录；两者保持当前代码", async () => {
  const requests = [];
  globalThis.fetch = async (_url, options) => {
    requests.push(JSON.parse(options.body));
    return json({ id: 43 });
  };
  await api.submit("PTV-0001", "new code", 42);
  await api.submit("PTV-0001", "first code");
  assert.deepEqual(requests, [
    { problem_pid: "PTV-0001", code: "new code", parent_submission_id: 42 },
    { problem_pid: "PTV-0001", code: "first code" },
  ]);
});

test("非 JSON 服务器错误不会把 HTML 或内部堆栈当成学生提示", async () => {
  globalThis.fetch = async () => new Response("<html>private internal trace</html>", { status: 502 });
  await assert.rejects(api.evidence(42), (error) =>
    error.status === 502 && !error.message.includes("private internal trace"));
});

test("异步提交只申请持久任务，202响应可查询；重试原提交不伪造新代码或父记录", async () => {
  const requests = [];
  const signal = new AbortController().signal;
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    return json({ id: 42, stage: "QUEUED", verdict: null, job: { status: "queued" } }, 202);
  };
  const accepted = await api.submitAsync("PTV-0001", "code", 21, signal);
  assert.equal(accepted.verdict, null);
  assert.equal(requests[0].options.headers.Prefer, "respond-async");
  assert.equal(requests[0].options.signal, signal);
  assert.equal(JSON.parse(requests[0].options.body).parent_submission_id, 21);
  await api.retrySubmission(42, signal);
  assert.equal(requests[1].url, "/api/v1/submissions/42/retry");
  assert.equal(requests[1].options.body, undefined);
});

test("账号切换后的迟到401不清除新账号凭证，也不让旧任务响应污染新页面", async () => {
  let complete;
  globalThis.fetch = () => new Promise((resolve) => { complete = resolve; });
  const request = api.getSubmission(42);
  storage.set("oi_token", "new-account-token");
  complete(json({ detail: "expired" }, 401));
  await assert.rejects(request, (error) => error.name === "AbortError");
  assert.equal(storage.get("oi_token"), "new-account-token");
  assert.deepEqual(events, []);
});
