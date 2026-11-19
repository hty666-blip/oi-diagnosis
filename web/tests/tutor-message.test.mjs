import assert from "node:assert/strict";
import { test } from "node:test";
import { sourceModule } from "./load-source.mjs";

const { sendMessageWithHistory } = await sourceModule("../src/tutor-message.ts");
const { questionDraftStore } = await sourceModule("../src/question-draft.ts");
const reply = { session_id: 8, status: "open", level: 2, action: "explain", reply: "检查累计更新。", accepted: true, replayed: false };
const turns = [
  { role: "tutor", level: 0, content: "先看你的代码。" },
  { role: "student", level: 1, content: "还是不会" },
  { role: "tutor", level: 2, content: "检查累计更新。" },
];
const history = { session_id: 8, submission_id: 4, status: "open", level: 2, turns };
function draftBrowser() {
  const values = new Map();
  const store = questionDraftStore({
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  }, 1, 4, () => "legacy-question-id");
  return { store, draft: store.write("还是不会") };
}

test("回复丢失后用原问题编号重试，历史已含消息时只显示一对问答", async () => {
  const { store, draft } = draftBrowser();
  const requests = [];
  let first = true;
  const send = async () => {
    requests.push(store.read());
    if (first) { first = false; throw new Error("响应正文丢失"); }
    return { ...reply, replayed: true };
  };
  await assert.rejects(sendMessageWithHistory({ send, refresh: async () => history, isCurrent: () => true }));
  assert.deepEqual(store.read(), draft);
  const result = await sendMessageWithHistory({ send, refresh: async () => history, isCurrent: () => true });
  assert.deepEqual(requests, [draft, draft]);
  assert.deepEqual(result.messages.map((m) => m.text), turns.map((t) => t.content));
  assert.equal(result.messages.filter((m) => m.role === "student").length, 1);
  assert.equal(result.accepted, true);
  store.acknowledge(draft.id);
  assert.equal(store.read().content, "");
});

test("重放旧回复后采用最新核验状态、绑定提交和完整历史，不回退到旧 open", async () => {
  const current = { ...history, status: "resolved", level: 4, verified_submission_id: 9, verdict: "AC",
    turns: [...turns, { role: "tutor", level: 4, content: "核验提交 #9：已通过。" }] };
  const result = await sendMessageWithHistory({
    send: async () => ({ ...reply, replayed: true }), refresh: async () => current, isCurrent: () => true,
  });
  assert.equal(result.session.status, "resolved");
  assert.equal(result.session.level, 4);
  assert.equal(result.session.verified_submission_id, 9);
  assert.equal(result.session.verdict, "AC");
  assert.equal(result.messages.at(-1).text, "核验提交 #9：已通过。");
});

test("新回复也同步权威历史，包含另一页面后来完成的提问", async () => {
  const current = { ...history, turns: [...turns,
    { role: "student", level: 2, content: "另一页面的新问题" },
    { role: "tutor", level: 3, content: "新的解释" }] };
  const result = await sendMessageWithHistory({ send: async () => reply, refresh: async () => current, isCurrent: () => true });
  assert.equal(result.messages.length, 5);
  assert.equal(result.messages.at(-1).text, "新的解释");
});

test("消息已提交但历史读取失败时保留问题和编号，再次同步成功后才确认", async () => {
  const { store, draft } = draftBrowser();
  await assert.rejects(sendMessageWithHistory({
    send: async () => reply, refresh: async () => { throw new Error("读取中断"); }, isCurrent: () => true,
  }), /问题已保留/);
  assert.deepEqual(store.read(), draft);
  const result = await sendMessageWithHistory({
    send: async () => ({ ...reply, replayed: true }), refresh: async () => history, isCurrent: () => true,
  });
  if (result.accepted) store.acknowledge(draft.id);
  assert.equal(store.read().content, "");
});

test("会话已关闭而未保存新问题时只同步历史，不伪造已发送气泡或清理草稿", async () => {
  const { store, draft } = draftBrowser();
  const result = await sendMessageWithHistory({
    send: async () => ({ ...reply, accepted: false, action: "closed", reply: "本次会话已经结束。" }),
    refresh: async () => ({ ...history, status: "blocked", turns: turns.slice(0, 1) }), isCurrent: () => true,
  });
  assert.equal(result.accepted, false);
  assert.equal(result.session.status, "blocked");
  assert.equal(result.messages.length, 1);
  assert.equal(result.notice, "本次会话已经结束。");
  if (result.accepted) store.acknowledge(draft.id);
  assert.deepEqual(store.read(), draft);
});

test("关闭状态查询失败仍保留草稿，不把拒收提示当作已保存回复", async () => {
  await assert.rejects(sendMessageWithHistory({
    send: async () => ({ ...reply, action: "closed", accepted: false }),
    refresh: async () => { throw new Error("断连"); }, isCurrent: () => true,
  }), /会话状态同步失败/);
});

test("发送期间离开旧组件后不再读取历史或向新组件交付结果", async () => {
  let current = true, refreshed = 0;
  const result = await sendMessageWithHistory({
    send: async () => { current = false; return reply; },
    refresh: async () => { refreshed++; return history; }, isCurrent: () => current,
  });
  assert.equal(result, null);
  assert.equal(refreshed, 0);
});

test("历史加载期间重新开启会话后，迟到成功与失败均不覆盖新会话", async () => {
  for (const fails of [false, true]) {
    let current = true;
    const result = await sendMessageWithHistory({
      send: async () => reply,
      refresh: async () => { current = false; if (fails) throw new Error("旧读取失败"); return history; },
      isCurrent: () => current,
    });
    assert.equal(result, null);
  }
});

test("会话历史编号不匹配时拒绝采用，不确认当前问题", async () => {
  await assert.rejects(sendMessageWithHistory({
    send: async () => reply, refresh: async () => ({ ...history, session_id: 99 }), isCurrent: () => true,
  }), /会话记录与本次提问不一致/);
});
