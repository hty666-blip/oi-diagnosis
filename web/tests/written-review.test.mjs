import assert from "node:assert/strict";
import { test } from "node:test";
import { sourceModule } from "./load-source.mjs";

const { createWrittenReviewIntent } = await sourceModule("../src/written-review.ts");

test("审核响应丢失后复用原编号，修改内容、动作或版本后更换编号", () => {
  let sequence = 0;
  const intent = createWrittenReviewIntent(() => `request-${++sequence}`);
  const body = { expected_revision: 0, action: "save", note: "人工核对", draft: { stem: "自编题" }, resolutions: {}, rights_confirmed: false };
  const first = intent(body);
  assert.equal(intent(structuredClone(body)).request_id, first.request_id);
  const changed = intent({ ...body, draft: { stem: "修订题干" } });
  assert.notEqual(changed.request_id, first.request_id);
  const approved = intent({ ...body, action: "approve", rights_confirmed: true });
  assert.notEqual(approved.request_id, changed.request_id);
  assert.notEqual(intent({ ...body, expected_revision: 1 }).request_id, approved.request_id);
});
