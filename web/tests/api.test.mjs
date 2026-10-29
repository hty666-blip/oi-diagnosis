import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { sourceModule } from "./load-source.mjs";

const { api, ApiError, setSession, clearSession, cachedUser, checkSessionStorage } = await sourceModule("../src/api.ts");
const firstUser = { id: 1, username: "student01", display_name: "学生一", role: "student", class_name: "测试班" };
const secondUser = { ...firstUser, id: 2, username: "student02", display_name: "学生二" };
let storage;
let events;
const originalFetch = globalThis.fetch;

beforeEach(() => {
  storage = new Map([["oi-draft:1:PTV-0001", "unsaved code"]]);
  events = [];
  globalThis.localStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  };
  globalThis.window = { dispatchEvent: (event) => { events.push(event.type); return true; } };
  setSession("local-test-token", firstUser);
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
  assert.deepEqual(events, ["oi-session-changed"]);
});

test("取消原提交不携带新代码，队列摘要使用授权查询并支持停止请求", async () => {
  const requests = [];
  const signal = new AbortController().signal;
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    return json(url.endsWith("/cancel") ? { id: 42, stage: "CANCELLED", verdict: null } : { counts: { queued: 1 } });
  };
  const cancelled = await api.cancelSubmission(42, signal);
  assert.equal(cancelled.stage, "CANCELLED");
  assert.equal(requests[0].url, "/api/v1/submissions/42/cancel");
  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].options.body, undefined);
  await api.coachJudging(signal);
  assert.equal(requests[1].url, "/api/v1/coach/judging");
  assert.equal(requests[1].options.method, "GET");
  for (const { options } of requests) {
    assert.equal(options.signal, signal);
    assert.equal(options.headers.Authorization, "Bearer local-test-token");
  }
});

test("另一标签切号尚未通知时，旧页提交在联网前被拒绝，不串用新token", async () => {
  let calls = 0;
  globalThis.fetch = async () => { calls++; return json({ id: 43 }); };
  storage.set("oi_token", "second-account-token");
  storage.set("oi_user", JSON.stringify(secondUser));
  await assert.rejects(api.submitAsync("PTV-0001", "old-account-draft"), (error) => error.name === "AbortError");
  assert.equal(calls, 0);
  assert.equal(cachedUser(), null);
  assert.equal(storage.get("oi_token"), "second-account-token");
  assert.equal(storage.get("oi-draft:1:PTV-0001"), "unsaved code");
  clearSession();
  assert.equal(storage.get("oi_token"), "second-account-token");
  assert.equal(storage.get("oi_user"), JSON.stringify(secondUser));
  assert.deepEqual(events, ["oi-session-changed"]);
});

test("storage失效不会自动激活新账号，公共登录仍可用，明确登录后才恢复受保护请求", async () => {
  storage.set("oi_token", "second-account-token");
  storage.set("oi_user", JSON.stringify(secondUser));
  checkSessionStorage();
  checkSessionStorage();
  assert.equal(cachedUser(), null);
  assert.deepEqual(events, ["oi-session-changed"]);
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    return json(url.endsWith("/login") ? { access_token: "explicit-token", user: secondUser } : []);
  };
  await api.demoAccounts();
  const login = await api.login("student02", "password");
  assert.equal(cachedUser(), null);
  await assert.rejects(api.mySubmissions(), (error) => error.name === "AbortError");
  assert.equal(requests.length, 2);
  assert.ok(requests.every(({ options }) => options.headers.Authorization === undefined));
  setSession(login.access_token, login.user);
  await api.mySubmissions();
  assert.equal(cachedUser().id, 2);
  assert.equal(requests[2].options.headers.Authorization, "Bearer explicit-token");
});

test("只变更共享身份资料也会失效；旧页直接退出不会清掉新会话", async () => {
  storage.set("oi_user", JSON.stringify(secondUser));
  checkSessionStorage();
  assert.equal(cachedUser(), null);
  assert.deepEqual(events, ["oi-session-changed"]);
  setSession("first-token", firstUser);
  storage.set("oi_token", "second-token");
  storage.set("oi_user", JSON.stringify(secondUser));
  clearSession();
  assert.equal(storage.get("oi_token"), "second-token");
  assert.equal(cachedUser(), null);
});

test("响应正文延迟到达时再次核对身份，不向重新登录后的页面交付旧数据", async () => {
  let complete;
  globalThis.fetch = async () => ({ ok: true, status: 200,
    json: () => new Promise((resolve) => { complete = resolve; }) });
  const pending = api.getSubmission(42);
  await new Promise((resolve) => setImmediate(resolve));
  setSession("second-account-token", secondUser);
  complete({ id: 42, code: "old-account-code" });
  await assert.rejects(pending, (error) => error.name === "AbortError");
  assert.equal(cachedUser().id, 2);
  assert.equal(storage.get("oi_token"), "second-account-token");
  assert.deepEqual(events, []);
});

test("公共演示接口失败不能退出当前账号，正文读取中止后也不交付结果", async () => {
  globalThis.fetch = async () => json({ detail: "unavailable" }, 401);
  await assert.rejects(api.demoAccounts(), (error) => error.status === 401);
  assert.equal(cachedUser().id, 1);
  assert.equal(storage.get("oi_token"), "local-test-token");
  assert.deepEqual(events, []);
  let complete;
  const controller = new AbortController();
  globalThis.fetch = async () => ({ ok: true, status: 200,
    json: () => new Promise((resolve) => { complete = resolve; }) });
  const pending = api.getSubmission(42, controller.signal);
  await new Promise((resolve) => setImmediate(resolve));
  controller.abort();
  complete({ id: 42 });
  await assert.rejects(pending, (error) => error.name === "AbortError");
  assert.equal(cachedUser().id, 1);
});
