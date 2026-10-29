import assert from "node:assert/strict";
import { test } from "node:test";
import { sourceModule } from "./load-source.mjs";

const { executionLabel } = await sourceModule("../src/evidence-state.ts");

test("运行 Accepted 不等于答案通过，旧记录保留核对边界", () => {
  assert.equal(executionLabel({ status: "Accepted", matched: false }), "输出不匹配");
  assert.equal(executionLabel({ status: "Accepted", matched: true }), "输出匹配");
  assert.equal(executionLabel({ status: "Accepted" }), "程序运行结束，需核对输出");
});

test("执行异常不会被匹配标记伪装成通过", () => {
  assert.equal(executionLabel({ status: "Time Limit Exceeded", matched: true }), "运行超时");
  assert.equal(executionLabel({ status: "Accepted", exit_code: 1, matched: true }), "运行异常退出");
  assert.equal(executionLabel({ status: "Internal Error" }), "执行器异常，验证未完成");
});
