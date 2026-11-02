export interface QuestionDraft { id: string; content: string }
interface DraftStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}
export const emptyQuestionDraft: QuestionDraft = { id: "", content: "" };

export function questionDraftStore(storage: DraftStorage, userId: number, submissionId: number,
  newId: () => string = () => crypto.randomUUID()) {
  const key = `oi-question-draft:${userId}:${submissionId}`;
  function read(): QuestionDraft {
    try {
      const value = JSON.parse(storage.getItem(key) ?? "null");
      if (value && typeof value.id === "string" && value.id.length > 0 && value.id.length <= 100
        && typeof value.content === "string" && value.content.length <= 4000) return value;
    } catch { /* 无可用存储或损坏的旧草稿不阻断提问。 */ }
    return emptyQuestionDraft;
  }
  return {
    read,
    write(content: string): QuestionDraft {
      const draft = { id: newId(), content };
      try { storage.setItem(key, JSON.stringify(draft)); } catch { /* 禁用存储时仍能编辑与发送。 */ }
      return draft;
    },
    acknowledge(id: string) {
      // 旧页面的迟到响应不能删除另一页面后来输入的问题，即使正文恰好相同。
      if (id && read().id === id) {
        try { storage.removeItem(key); } catch { /* 保持发送结果可用。 */ }
      }
    },
  };
}
