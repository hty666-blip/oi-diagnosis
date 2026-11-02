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

test("题库预览确认只发送预览编号，提交携带当前题面版本并区分历史读取上下文", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, body: options.body && JSON.parse(options.body) }); return json({ preview_id: "stored-preview" }); };
  const preview = await api.catalogPreview([{ pid: "SYNTHETIC" }], true);
  await api.catalogConfirm(preview.preview_id);
  assert.deepEqual(calls[1].body, { preview_id: "stored-preview" });
  await api.getProblem("SYNTHETIC", 7);
  await api.getProblem("SYNTHETIC", undefined, 42);
  assert.equal(calls[2].url, "/api/v1/problems/SYNTHETIC?training_id=7");
  assert.equal(calls[3].url, "/api/v1/problems/SYNTHETIC?submission_id=42");
  await api.submitAsync("SYNTHETIC", "changed code", "cpp17", 42, undefined, 7, 9);
  assert.equal(calls[4].body.problem_version_id, 9);
  assert.equal(calls[4].body.parent_submission_id, 42);
  assert.equal(calls[4].body.training_id, 7);
});

test("题包错误指明题目和测试行，避免只显示无法定位的字段错误", async () => {
  globalThis.fetch = async () => json({ detail: [{ loc: ["body", "problems", 1, "cases", 2, "output"], msg: "Field required" }] }, 422);
  await assert.rejects(api.catalogPreview([], true), (e) => e.message.includes("题目 / 2 / 正式测试 / 3 / 预期输出"));
});

test("转班预览与确认分开，账号和班级变更携带服务器预期版本", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, method: options.method, body: JSON.parse(options.body), auth: options.headers.Authorization }); return json({}); };
  await api.transferPreview(2, { expected_version: 4, class_id: 8 });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/v1/admin/users/2/transfer/preview");
  await api.transferStudent(2, { expected_version: 4, class_id: 8 });
  assert.equal(calls[1].url, "/api/v1/admin/users/2/transfer");
  await api.editClass(8, { name: "改名", expected_version: 7, archived: false, teacher_ids: [3], transfer_to: 3 });
  assert.equal(calls[2].method, "PATCH");
  assert.equal(calls[2].body.expected_version, 7);
  assert.equal(calls[2].body.transfer_to, 3);
  assert.ok(calls.every((c) => c.auth === "Bearer local-test-token"));
});

test("改密响应读取期间换号时，不向旧页面交付新的登录凭证", async () => {
  let finish, started;
  const entered = new Promise((resolve) => { started = resolve; });
  globalThis.fetch = async () => {
    const response = json({});
    response.json = () => { started(); return new Promise((resolve) => { finish = resolve; }); };
    return response;
  };
  const pending = api.changePassword("old-password", "new-password");
  await entered;
  setSession("second-token", secondUser);
  finish({ access_token: "late-old-user-token", user: firstUser });
  await assert.rejects(pending, (e) => e.name === "AbortError");
  assert.equal(cachedUser().id, secondUser.id);
});

test("个别反馈重试保留请求编号，标记查看是独立操作，补练明确指定接收人", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, body: options.body && JSON.parse(options.body) }); return json({ items: [] }); };
  await api.sendTrainingFeedback(7, 2, "逐轮检查累计值。", "b7b1cce6-a0bf-4493-aeab-5b42ad80b197", 42);
  await api.sendTrainingFeedback(7, 2, "逐轮检查累计值。", "b7b1cce6-a0bf-4493-aeab-5b42ad80b197", 42);
  await api.markFeedbackRead(7, 9);
  await api.saveTraining({ title: "补练", class_name: "A", problems: [{ pid: "PTV-0001", required: true }], student_ids: [2], source_training_id: 7 });
  assert.deepEqual(calls[0], calls[1]);
  assert.equal(calls[2].url, "/api/v1/trainings/7/feedback/9/read");
  assert.deepEqual(calls[3].body.student_ids, [2]);
  assert.equal(calls[3].body.source_training_id, 7);
});

test("档案导出正文迟到且账号切换后不生成下载", async () => {
  let finish, started;
  const entered = new Promise((resolve) => { started = resolve; });
  globalThis.fetch = async () => {
    const response = json({});
    response.blob = () => { started(); return new Promise((resolve) => { finish = resolve; }); };
    return response;
  };
  const pending = api.downloadStudentProfile(1);
  await entered;
  setSession("second-test-token", secondUser);
  finish(new Blob(["private report"]));
  await assert.rejects(pending, /账号|会话|登录/);
});

test("讲评草稿保留正文换行、依据摘要和预期版本；生成与发布独立请求", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, body: options.body && JSON.parse(options.body) }); return json({ published: null }); };
  await api.trainingLesson(7);
  await api.generateTrainingLesson(7, 2);
  await api.saveTrainingLesson(7, 3, "讲评\n```cpp\nsum += x;\n```", "a".repeat(64));
  await api.publishTrainingLesson(7, 4);
  assert.deepEqual(calls.map((c) => c.url), ["/api/v1/trainings/7/lesson", "/api/v1/trainings/7/lesson/generate", "/api/v1/trainings/7/lesson/draft", "/api/v1/trainings/7/lesson/publish"]);
  assert.deepEqual(calls[2].body, { expected_revision: 3, content: "讲评\n```cpp\nsum += x;\n```", basis_digest: "a".repeat(64) });
  assert.deepEqual(calls[3].body, { expected_revision: 4 });
});

test("训练提交显式携带任务编号，自由练习不带；发布与草稿是不同操作", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, body: options.body && JSON.parse(options.body) }); return json({ id: 1 }); };
  await api.submitAsync("PTV-0001", "code", "cpp17", undefined, undefined, 7);
  await api.submitAsync("PTV-0001", "code", "cpp17");
  assert.equal(calls[0].body.training_id, 7);
  assert.equal(Object.hasOwn(calls[1].body, "training_id"), false);
  const draft = { title: "训练", class_name: "A", problems: [{ pid: "PTV-0001", required: true }] };
  await api.saveTraining(draft);
  await api.saveTraining(draft, 7);
  await api.publishTraining(7);
  assert.deepEqual(calls.slice(2).map((c) => c.url), ["/api/v1/trainings", "/api/v1/trainings/7/draft", "/api/v1/trainings/7/publish"]);
  assert.deepEqual(calls[2].body, draft);
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

test("核验的结构化原因保留状态码与恢复标识，不丢失用户能理解的说明", async () => {
  globalThis.fetch = async () => json({ detail: {
    code: "verification_unchanged", message: "提交 #3 与答疑起点 #2 的代码和语言相同。请修改上方代码后重新提交。",
  } }, 409);
  await assert.rejects(api.verifySession(1, 3), (error) => error instanceof ApiError
    && error.status === 409 && error.code === "verification_unchanged"
    && error.message.includes("提交 #3") && error.message.includes("答疑起点 #2"));
  assert.equal(storage.get("oi-draft:1:PTV-0001"), "unsaved code");
  assert.equal(storage.get("oi_token"), "local-test-token");
});

test("多语言重新提交同时携带父记录与语言，首次提交不伪造父记录", async () => {
  const requests = [];
  globalThis.fetch = async (_url, options) => {
    requests.push(JSON.parse(options.body));
    return json({ id: 43 });
  };
  await api.submit("PTV-0001", "new code", "python3", 42);
  await api.submit("PTV-0001", "first code", "c11");
  assert.deepEqual(requests, [
    { problem_pid: "PTV-0001", code: "new code", language: "python3", parent_submission_id: 42 },
    { problem_pid: "PTV-0001", code: "first code", language: "c11" },
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
  const accepted = await api.submitAsync("PTV-0001", "code", "java17", 21, signal);
  assert.equal(accepted.verdict, null);
  assert.equal(requests[0].options.headers.Prefer, "respond-async");
  assert.equal(requests[0].options.signal, signal);
  assert.deepEqual(JSON.parse(requests[0].options.body), {
    problem_pid: "PTV-0001", code: "code", language: "java17", parent_submission_id: 21,
  });
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
  await assert.rejects(api.submitAsync("PTV-0001", "old-account-draft", "cpp17"), (error) => error.name === "AbortError");
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
