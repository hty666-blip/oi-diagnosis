import assert from "node:assert/strict";
import { test } from "node:test";
import { sourceModule } from "./load-source.mjs";

const { indentedNewline, shouldSendMessage } = await sourceModule("../src/editor-input.ts");

test("换行继承缩进，括号中间展开并将光标留在内层", () => {
  const source = "  if (ok) {}";
  const cursor = source.indexOf("}");
  const next = indentedNewline(source, cursor, cursor, "cpp17");
  assert.equal(next.value, "  if (ok) {\n    \n  }");
  assert.equal(next.cursor, next.value.indexOf("\n") + 5);
  assert.deepEqual(indentedNewline("    sum += x;", 13, 13, "c11"), {
    value: "    sum += x;\n    ", cursor: 18,
  });
});

test("Python 冒号增加缩进，已有制表符保留；选择替换不丢后文", () => {
  assert.equal(indentedNewline("if ok:", 6, 6, "python3").value, "if ok:\n    ");
  assert.equal(indentedNewline("\tif (ok) {", 10, 10, "java17").value, "\tif (ok) {\n\t\t");
  assert.equal(indentedNewline("  abcd", 3, 5, "cpp17").value, "  a\n  d");
  assert.equal(indentedNewline('print(":")', 10, 10, "python3").value, 'print(":")\n');
});

test("聊天回车发送，Shift 换行，中文输入确认不发送", () => {
  const enter = { key: "Enter", shiftKey: false, isComposing: false, keyCode: 13 };
  assert.equal(shouldSendMessage(enter), true);
  assert.equal(shouldSendMessage({ ...enter, shiftKey: true }), false);
  assert.equal(shouldSendMessage({ ...enter, isComposing: true }), false);
  assert.equal(shouldSendMessage({ ...enter, keyCode: 229 }), false);
  assert.equal(shouldSendMessage({ ...enter, key: "a" }), false);
});
