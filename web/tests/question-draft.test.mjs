import assert from "node:assert/strict";
import { test } from "node:test";
import { sourceModule } from "./load-source.mjs";

const { questionDraftStore } = await sourceModule("../src/question-draft.ts");
function browser() {
  const values = new Map();
  let sequence = 0;
  const storage = { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
  return { values, storage, store: (user = 1, submission = 4) => questionDraftStore(storage, user, submission, () => String(++sequence)) };
}

test("未发送的问题在页面重新创建后恢复，保留换行和原始输入", () => {
  const b = browser(), text = "我不会，sum = x 怎么执行？\n  请逐句讲。 ";
  b.store().write(text);
  assert.equal(b.store().read().content, text);
});

test("切换学生或提交不会读取他人的问题，切回原记录仍可继续", () => {
  const b = browser();
  b.store(1, 4).write("学生甲的问题");
  b.store(2, 4).write("学生乙的问题");
  b.store(1, 5).write("另一份代码的问题");
  assert.equal(b.store(1, 4).read().content, "学生甲的问题");
  assert.equal(b.store(2, 4).read().content, "学生乙的问题");
  assert.equal(b.store(1, 5).read().content, "另一份代码的问题");
});

test("只有发送成功确认才清理当前问题，不影响其他记录", () => {
  const b = browser(), store = b.store();
  const sent = store.write("已发送的问题");
  b.store(2).write("别人的问题");
  assert.equal(b.store().read().content, sent.content); // 失败和重新登录没有成功确认，继续保留。
  store.acknowledge(sent.id);
  assert.equal(b.store().read().content, "");
  assert.equal(b.store(2).read().content, "别人的问题");
});

test("旧请求成功迟到不能擦掉新页面写入的问题，即使内容相同", () => {
  const b = browser(), oldPage = b.store(), newPage = b.store();
  const sent = oldPage.write("还是不会");
  const later = newPage.write("还是不会");
  oldPage.acknowledge(sent.id);
  assert.deepEqual(newPage.read(), later);
  newPage.acknowledge(later.id);
  assert.equal(newPage.read().content, "");
});

test("损坏或超长草稿不阻止重新提问", () => {
  const b = browser(); b.store().write("原问题");
  const key = [...b.values.keys()][0];
  for (const value of ["{bad-json", "null", JSON.stringify({id: "old", content: 123}), JSON.stringify({id: "old", content: "x".repeat(4001)})]) {
    b.values.set(key, value);
    assert.equal(b.store().read().content, "");
  }
  b.store().write("重新输入的问题");
  assert.equal(b.store().read().content, "重新输入的问题");
});

test("浏览器存储不可用时仍允许编辑和确认发送", () => {
  const storage = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("quota"); }, removeItem() { throw new Error("blocked"); } };
  const store = questionDraftStore(storage, 1, 4, () => "request");
  assert.equal(store.read().content, "");
  assert.equal(store.write("仍能输入").content, "仍能输入");
  assert.doesNotThrow(() => store.acknowledge("request"));
});
