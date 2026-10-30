import assert from "node:assert/strict";
import { test } from "node:test";
import { sourceModule } from "./load-source.mjs";

const { resultState } = await sourceModule("../src/result-state.ts");

test("历史计数无法核验时明确待核对，不显示通过或宣称没有测试", () => {
  assert.deepEqual(resultState("UNVERIFIED", { scope: "package", passed: 1, total: true }),
    { label: "历史结果待核对", tone: "warn" });
});

test("样例、零测试、旧 AC 以及不一致计数均不呈现正式通过", () => {
  const cases = [
    ["SAMPLE_PASS", { scope: "samples", passed: 2, total: 2 }],
    ["NO_TESTS", { scope: "package", passed: 0, total: 0 }],
    ["AC", undefined],
    ["AC", { scope: "samples", passed: 2, total: 2 }],
    ["AC", { scope: "package", passed: 0, total: 0 }],
    ["AC", { scope: "package", passed: 2, total: 3 }],
    ["AC", { scope: "package", passed: 1.5, total: 1.5 }],
  ];
  for (const [verdict, scope] of cases) assert.notEqual(resultState(verdict, scope).tone, "ok");
});

test("只有正式题包的非零全部通过显示成功，实际运行失败仍是失败", () => {
  const tests = { scope: "package", passed: 3, total: 3 };
  assert.equal(resultState("AC", tests).tone, "ok");
  assert.equal(resultState("AC", tests).label, "题包测试通过");
  assert.equal(resultState("WA", tests).tone, "bad");
  assert.equal(resultState(null, tests).tone, "warn");
});
