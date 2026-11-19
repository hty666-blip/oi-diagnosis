import { readLocal, removeLocal, writeLocal, type WrittenAttemptView, type WrittenResponse } from "./api";

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export type WrittenList = "bank" | "papers" | "knowledge" | "progress" | "assignments" | WrittenAttemptView;
type ExamOrigin = { examId?: number; returnExamId?: number };
export type WrittenTarget =
  | ({ kind: "question"; id: number; version?: string } & ExamOrigin)
  | ({ kind: "attempt"; id: number } & ExamOrigin)
  | { kind: "exam"; id: number }
  | { kind: "assignment"; id: number };
export interface WrittenPosition {
  list: WrittenList;
  target: WrittenTarget | null;
}

const positionKey = (userId: number) => `oi-written-position:${userId}`;
const entryKey = (userId: number) => `oi-written-open:${userId}`;
const examQuestionKey = (userId: number, examId: number) => `oi-written-exam-question:${userId}:${examId}`;

export function readExamQuestion(userId: number, examId: number): number | undefined {
  try {
    const value = Number(sessionStorage.getItem(examQuestionKey(userId, examId)));
    if (Number.isSafeInteger(value) && value > 0) return value;
  } catch { /* 无存储时从第一题打开。 */ }
}

export function rememberExamQuestion(userId: number, examId: number, questionId: number) {
  try { sessionStorage.setItem(examQuestionKey(userId, examId), String(questionId)); }
  catch { /* 无存储时仍可切换题目。 */ }
}

export function wasWrittenOpen(userId: number): boolean {
  try { return sessionStorage.getItem(entryKey(userId)) === "1"; } catch { return false; }
}

export function rememberWrittenOpen(userId: number, open: boolean) {
  try {
    if (open) sessionStorage.setItem(entryKey(userId), "1");
    else sessionStorage.removeItem(entryKey(userId));
  } catch { /* 可正常导航。 */ }
}

export function readWrittenPosition(userId: number): WrittenPosition {
  try {
    const value = JSON.parse(sessionStorage.getItem(positionKey(userId)) ?? "null");
    if (value?.list === "assignments") {
      const target = value.target;
      return { list: "assignments", target: target?.kind === "assignment" && Number.isSafeInteger(target.id) && target.id > 0
        ? { kind: "assignment", id: target.id } : null };
    }
    if (value && ["bank", "papers", "knowledge", "progress", "history", "wrong", "corrected"].includes(value.list)) {
      const target = value.target;
      if (target && Number.isSafeInteger(target.id) && target.id > 0) {
        const fromExam: ExamOrigin = {};
        if (value.list === "papers") {
          if (Number.isSafeInteger(target.examId) && target.examId > 0) fromExam.examId = target.examId;
          else if (Number.isSafeInteger(target.returnExamId) && target.returnExamId > 0) fromExam.returnExamId = target.returnExamId;
        }
        if (target.kind === "attempt") return { list: value.list, target: { kind: "attempt", id: target.id, ...fromExam } };
        if (target.kind === "exam" && value.list === "papers") return { list: "papers", target: { kind: "exam", id: target.id } };
        if (target.kind === "question") {
          const version = typeof target.version === "string" && uuid.test(target.version) ? target.version : undefined;
          return { list: value.list, target: { kind: "question", id: target.id, version, ...fromExam } };
        }
      }
      return { list: value.list, target: null };
    }
  } catch { /* 损坏的本地导航记录回到题库。 */ }
  return { list: "bank", target: null };
}

export function rememberWrittenPosition(userId: number, position: WrittenPosition) {
  try { sessionStorage.setItem(positionKey(userId), JSON.stringify(position)); } catch { /* 可正常导航。 */ }
}

export interface WrittenAnswerDraft {
  requestId: string;
  response: WrittenResponse;
}

function readResponse(value: unknown): WrittenResponse | null {
  if (!value || typeof value !== "object" || !("kind" in value)) return null;
  if (value.kind === "boolean" && "value" in value && typeof value.value === "boolean") {
    return { kind: "boolean", value: value.value };
  }
  if (value.kind === "text" && "value" in value && typeof value.value === "string" && value.value.length <= 4000) {
    return { kind: "text", value: value.value };
  }
  if ((value.kind === "option" || value.kind === "options") && "option_ids" in value
    && Array.isArray(value.option_ids) && value.option_ids.length <= 26
    && value.option_ids.every((id: unknown) => typeof id === "string" && id.length <= 16)) {
    return { kind: value.kind, option_ids: value.option_ids };
  }
  return null;
}

export function writtenAnswerStore(userId: number, questionId: number, version: string, assignmentId?: number, examId?: number) {
  const key = examId !== undefined ? `oi-written-exam-retry-draft:${userId}:${examId}:${questionId}:${version}`
    : assignmentId === undefined
    ? `oi-written-draft:${userId}:${questionId}:${version}`
    : `oi-written-task-draft:${userId}:${assignmentId}:${questionId}:${version}`;
  const statusKey = (id: string) => `${key}:${id}`;
  return {
    read(): WrittenAnswerDraft | null {
      try {
        const saved = JSON.parse(readLocal(key) ?? "null");
        if (!saved || typeof saved.requestId !== "string" || !uuid.test(saved.requestId)) return null;
        const response = readResponse(saved.response);
        if (response && readLocal(statusKey(saved.requestId)) !== "done") {
          return { requestId: saved.requestId, response };
        }
      } catch { /* 可重新作答。 */ }
      return null;
    },
    write(response: WrittenResponse): WrittenAnswerDraft {
      const draft = { requestId: crypto.randomUUID(), response };
      writeLocal(key, JSON.stringify(draft));
      return draft;
    },
    isPending: (id: string) => readLocal(statusKey(id)) === "pending",
    markPending: (id: string) => writeLocal(statusKey(id), "pending"),
    markDone: (id: string) => writeLocal(statusKey(id), "done"),
    markRejected: (id: string) => removeLocal(statusKey(id)),
  };
}
