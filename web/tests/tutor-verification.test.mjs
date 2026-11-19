import assert from "node:assert/strict";
import { test } from "node:test";
import { sourceModule } from "./load-source.mjs";

const { sameSource, shouldVerifySubmission, verificationRecovery, verifyWithRefresh } = await sourceModule("../src/tutor-verification.ts");

test("已解决或暂停会话不因新提交反复核验，待核对绑定可接受新的正式核验", () => {
  for (const status of ["resolved", "blocked", "escalated", "unavailable"]) {
    assert.equal(shouldVerifySubmission(status, 1, 3, false), false);
  }
  assert.equal(shouldVerifySubmission("unverified", 1, 3, false), true);
  assert.equal(shouldVerifySubmission("open", 1, 3, false), true);
  assert.equal(shouldVerifySubmission("open", 1, 1, false), false);
  assert.equal(shouldVerifySubmission("open", 1, 3, true), false);
});

test("未修改的提交应返回编辑器；等待判题和网络故障仍可重试", () => {
  assert.equal(verificationRecovery({ status: 409, code: "verification_unchanged" }), "edit");
  assert.equal(verificationRecovery({ status: 409, code: "verification_not_new" }), "edit");
  assert.equal(verificationRecovery({ status: 409, code: "verification_pending" }), "retry");
  assert.equal(verificationRecovery(new Error("无法连接服务")), "retry");
  assert.equal(verificationRecovery({ status: 503 }), "retry");
  assert.equal(verificationRecovery({ status: 429 }), "retry");
});

test("无权限、会话消失或核验冲突不重复请求同一记录", () => {
  for (const status of [400, 403, 404, 409, 422]) {
    assert.equal(verificationRecovery({ status }), "restart");
  }
  assert.equal(verificationRecovery(new Error("请先修改代码并重新提交，再核验新提交")), "edit");
  assert.equal(verificationRecovery({ status: 409, message: "新提交尚未完成判题" }), "retry");
});

test("提交变化按后端相同规则比较换行、原始字符和语言", () => {
  assert.equal(sameSource("int x;\r\n", "cpp17", "int x;\n", "cpp17"), true);
  assert.equal(sameSource("int x;\n", "cpp17", "int x;\n", "c11"), false);
  assert.equal(sameSource('print("a b")', "python3", 'print("ab")', "python3"), false);
  assert.equal(sameSource("int x;", "cpp17", "long long x;", "cpp17"), false);
});

test("核验拒绝后读取服务器当前状态，保留原错误，不复用旧的已解决结论", async () => {
  const error = new Error("新提交尚未完成判题");
  const history = { session_id: 9, status: "unverified", verified_submission_id: 42 };
  const result = await verifyWithRefresh({
    verify: async () => { throw error; }, refresh: async () => history,
  });
  assert.equal(result.error, error);
  assert.equal(result.history, history);
  assert.equal(result.reply, undefined);
});

test("核验与状态读取均失败时保留错误，不伪造任何会话状态", async () => {
  const error = new Error("连接中断");
  const result = await verifyWithRefresh({
    verify: async () => { throw error; }, refresh: async () => { throw new Error("仍不可用"); },
  });
  assert.deepEqual(result, { error });
});

test("核验成功直接交付真实回复，不额外查询覆盖新结果", async () => {
  const reply = { session_id: 9, status: "resolved", verified_submission_id: 43 };
  const result = await verifyWithRefresh({
    verify: async () => reply, refresh: async () => { throw new Error("不应调用"); },
  });
  assert.deepEqual(result, { reply });
});
