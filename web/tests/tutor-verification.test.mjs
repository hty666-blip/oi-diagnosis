import assert from "node:assert/strict";
import { test } from "node:test";
import { sourceModule } from "./load-source.mjs";

const { verifyWithRefresh } = await sourceModule("../src/tutor-verification.ts");

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
