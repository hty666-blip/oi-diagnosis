export interface QuestionDraft { id: string; content: string }
interface DraftStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}
export const emptyQuestionDraft: QuestionDraft = { id: "", content: "" };

function canonicalId(value: string): string {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
    ? value.toLowerCase() : value;
}

export function questionDraftStore(storage: DraftStorage, userId: number, submissionId: number,
  newId: () => string = () => crypto.randomUUID()) {
  const key = `oi-question-draft:${userId}:${submissionId}`;
  const acknowledgedKey = (id: string) => `oi-question-draft-ack:${userId}:${submissionId}:${id}`;
  function write(content: string): QuestionDraft {
    const draft = { id: canonicalId(newId()), content };
    try { storage.setItem(key, JSON.stringify(draft)); } catch { /* 禁用存储时仍能编辑与发送。 */ }
    return draft;
  }
  function read(): QuestionDraft {
    try {
      const value = JSON.parse(storage.getItem(key) ?? "null");
      if (value && typeof value.id === "string" && value.id.length > 0 && value.id.length <= 100
        && typeof value.content === "string" && value.content.length <= 4000) {
        const id = canonicalId(value.id);
        // 读取不改写共享指针，避免旧缓存快照覆盖另一页面刚写入的新问题。
        let acknowledged = false;
        try { acknowledged = storage.getItem(acknowledgedKey(id)) === "1"; } catch { /* 未确认就保留输入。 */ }
        return acknowledged ? emptyQuestionDraft : { id, content: value.content };
      }
    } catch { /* 无可用存储或损坏的旧草稿不阻断提问。 */ }
    return emptyQuestionDraft;
  }
  return {
    read,
    write,
    acknowledge(id: string) {
      const canonical = canonicalId(id);
      if (!canonical || canonical.length > 100) return;
      // localStorage 没有跨页面比较并删除事务。只确认本编号，绝不删除或改写共享当前草稿。
      try { storage.setItem(acknowledgedKey(canonical), "1"); } catch { /* 保持发送结果可用。 */ }
    },
  };
}
