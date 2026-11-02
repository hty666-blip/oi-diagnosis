import assert from "node:assert/strict";
import { test } from "node:test";
import { sourceModule } from "./load-source.mjs";

const { questionDraftStore } = await sourceModule("../src/question-draft.ts");
const uuid = (sequence) => `00000000-0000-4000-8000-${String(sequence).padStart(12, "0")}`;
function browser() {
  const values = new Map();
  let sequence = 0;
  const storage = { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
  return { values, storage, store: (user = 1, submission = 4) => questionDraftStore(storage, user, submission, () => uuid(++sequence)) };
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

test("ACK 避开读取旧值后删除共享草稿的跨页面交错", () => {
  const b = browser(), oldPage = b.store(), newPage = b.store();
  const sent = oldPage.write("旧问题");
  const key = [...b.values.keys()][0], originalGet = b.storage.getItem;
  let interleave = true, newer;
  b.storage.getItem = (target) => {
    const captured = originalGet(target);
    if (target === key && interleave) {
      interleave = false;
      newer = newPage.write("另一页面刚写的问题");
    }
    return captured; // 返回读取瞬间的旧值，而共享存储已被另页更新。
  };
  oldPage.acknowledge(sent.id);
  // ACK 不读取共享草稿时没有读改写窗口，另一页的输入在其后正常发生。
  if (interleave) { interleave = false; newer = newPage.write("另一页面刚写的问题"); }
  assert.deepEqual(newPage.read(), newer);
});

test("旧快照与迟到 ACK 不会复活另一页面较新且已发送的问题", () => {
  const b = browser(), oldPage = b.store(), newPage = b.store();
  const sent = oldPage.write("旧问题");
  const key = [...b.values.keys()][0], originalGet = b.storage.getItem;
  let interleave = true;
  b.storage.getItem = (target) => {
    const captured = originalGet(target);
    if (target === key && interleave) {
      interleave = false;
      const newer = newPage.write("新问题，已经发送");
      newPage.acknowledge(newer.id);
    }
    return captured;
  };
  oldPage.read();
  oldPage.acknowledge(sent.id);
  assert.equal(newPage.read().content, "");
  oldPage.acknowledge(sent.id);
  assert.equal(newPage.read().content, "");
});

test("旧草稿编号保持兼容，读取不改写内容或存储指针", () => {
  const b = browser(); b.store().write("占位");
  const key = [...b.values.keys()][0];
  b.values.set(key, JSON.stringify({ id: "legacy-id", content: "仍要保留\n第二行问题" }));
  const draft = b.store().read();
  assert.equal(draft.id, "legacy-id");
  assert.equal(draft.content, "仍要保留\n第二行问题");
  assert.deepEqual(b.store().read(), draft);
  b.store().acknowledge(draft.id);
  assert.equal(b.store().read().content, "");
});

test("读取旧编号的快照不能覆盖同一时刻另一页面写入的新问题", () => {
  const b = browser(), oldPage = b.store(), newPage = b.store();
  oldPage.write("占位");
  const key = [...b.values.keys()][0];
  b.values.set(key, JSON.stringify({ id: "legacy-id", content: "旧问题" }));
  const originalGet = b.storage.getItem;
  let interleave = true, newer;
  b.storage.getItem = (target) => {
    const captured = originalGet(target);
    if (target === key && interleave) {
      interleave = false;
      newer = newPage.write("读取期间新写入的问题");
    }
    return captured;
  };
  assert.equal(oldPage.read().content, "旧问题");
  assert.deepEqual(newPage.read(), newer);
});

test("确认标记按账号和提交隔离，UUID 大小写一致处理", () => {
  const b = browser(), id = "ABCDEFAB-1234-4000-8000-000000000001";
  const first = questionDraftStore(b.storage, 1, 4, () => id);
  const otherUser = questionDraftStore(b.storage, 2, 4, () => id);
  const otherSubmission = questionDraftStore(b.storage, 1, 5, () => id);
  first.write("甲"); otherUser.write("乙"); otherSubmission.write("另一提交");
  first.acknowledge(id);
  assert.equal(first.read().content, "");
  assert.equal(otherUser.read().content, "乙");
  assert.equal(otherSubmission.read().content, "另一提交");
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
  const store = questionDraftStore(storage, 1, 4, () => uuid(1));
  assert.equal(store.read().content, "");
  assert.equal(store.write("仍能输入").content, "仍能输入");
  assert.doesNotThrow(() => store.acknowledge(uuid(1)));
});
