import { useEffect, useRef, useState } from "react";
import {
  api, ApiError, messageOf, type WrittenAttempt, type WrittenPublishedQuestion,
  type WrittenQuestionType, type WrittenReferenceAnswer, type WrittenResponse,
} from "../api";
import Markdown from "../Markdown";
import { writtenAnswerStore, type WrittenAnswerDraft } from "../written-practice";
import { ErrorNotice } from "./RecordUI";
import WrittenHelp from "./WrittenHelp";

export const writtenTypeLabel: Record<WrittenQuestionType, string> = {
  single_choice: "单选", multiple_choice: "多选", true_false: "判断", fill_blank: "填空",
};

function paperImages(question: WrittenPublishedQuestion) {
  const byPath = Object.entries(question.assets_by_path ?? {});
  const mappedUrls = new Set(byPath.map(([, url]) => url));
  return [...byPath, ...question.assets.filter((url) => !mappedUrls.has(url)).map((url) => [url, url])];
}

function useWrittenImages(question: WrittenPublishedQuestion) {
  const [attempt, setAttempt] = useState(0);
  const [files, setFiles] = useState<Map<string, string>>(new Map());
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(question.assets.length > 0);
  useEffect(() => {
    const items = paperImages(question);
    const controller = new AbortController();
    const urls = new Map<string, string>();
    setFiles(new Map());
    setError("");
    setLoading(items.length > 0);
    if (!items.length) return;
    void Promise.allSettled(items.map(async ([path, url]) => {
      const blob = await api.writtenPublishedAsset(url, controller.signal);
      if (!controller.signal.aborted) urls.set(path, URL.createObjectURL(blob));
    })).then((responses) => {
      if (controller.signal.aborted) return;
      setFiles(new Map(urls));
      setLoading(false);
      const rejected = responses.find((value) => value.status === "rejected");
      if (rejected?.status === "rejected") setError(messageOf(rejected.reason));
    });
    return () => {
      controller.abort();
      urls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [question, attempt]);
  return { files, loading, error, retry: () => setAttempt((x) => x + 1) };
}

function answerText(answer: WrittenReferenceAnswer) {
  if ("option_ids" in answer) return answer.option_ids.join("、");
  if ("accepted" in answer) return answer.accepted.map((value) => JSON.stringify(value)).join(" 或 ");
  if ("value" in answer) return typeof answer.value === "boolean"
    ? (answer.value ? "正确" : "错误")
    : JSON.stringify(answer.value);
  return "";
}

function ScoreBadge({ correct }: { correct: boolean }) {
  return <span className={`badge ${correct ? "ok" : "bad"}`}>{correct ? "答对" : "答错"}</span>;
}

export { ScoreBadge as WrittenScoreBadge };

function WrittenAnswers({ question, response, onAnswer, imageSource }: {
  question: WrittenPublishedQuestion;
  response: WrittenResponse | null;
  onAnswer: (response: WrittenResponse) => void;
  imageSource: (path: string) => string | undefined;
}) {
  const checked = response && "option_ids" in response ? response.option_ids : [];
  const multiple = question.question_type === "multiple_choice";
  if (question.question_type === "fill_blank") return <label className="written-answer-input">
    答案
    <textarea rows={3} maxLength={4000} value={response?.kind === "text" ? response.value : ""}
      onChange={(event) => onAnswer({ kind: "text", value: event.target.value })} />
  </label>;
  if (question.question_type === "true_false") return <div className="written-choice-list">
    {[true, false].map((value) => {
      const selected = response?.kind === "boolean" && response.value === value;
      return <label key={String(value)} className={`written-choice ${selected ? "selected" : ""}`}>
        <input type="radio" name="written-answer" checked={selected}
          onChange={() => onAnswer({ kind: "boolean", value })} />
        {value ? "正确" : "错误"}
      </label>;
    })}
  </div>;
  return <div className="written-choice-list">
    {question.options.map((option) => {
      const selected = checked.includes(option.id);
      const description = `written-answer-option-${question.id}-${option.id}`;
      const toggle = () => {
        const next = new Set(checked);
        if (selected) next.delete(option.id);
        else next.add(option.id);
        onAnswer(multiple
          ? { kind: "options", option_ids: question.options.map((item) => item.id).filter((id) => next.has(id)) }
          : { kind: "option", option_ids: [option.id] });
      };
      return <div key={option.id} className={`written-choice written-choice-option ${selected ? "selected" : ""}`}>
        <label>
          <input type={multiple ? "checkbox" : "radio"} name="written-answer"
            aria-describedby={description} checked={selected} onChange={toggle} />
          {option.id}
        </label>
        <div id={description}><Markdown text={option.text} imageSource={imageSource} /></div>
      </div>;
    })}
  </div>;
}

function hasAnswer(response: WrittenResponse | null, type: WrittenQuestionType) {
  if (!response) return false;
  if (type === "true_false") return response.kind === "boolean";
  if (type === "fill_blank") return response.kind === "text" && Boolean(response.value.trim());
  if (type === "single_choice") return response.kind === "option" && response.option_ids.length === 1;
  return response.kind === "options" && response.option_ids.length > 0;
}

function rejectionNeedsNewVersion(error: unknown) {
  return error instanceof ApiError && [
    "written_version_changed", "written_question_unavailable",
  ].includes(error.code ?? "");
}

function SubmissionForm({ question, userId, onSubmitted, onCheckVersion, imageSource }: {
  question: WrittenPublishedQuestion;
  userId: number;
  onSubmitted: (receipt: WrittenAttempt) => void;
  onCheckVersion: () => void;
  imageSource: (path: string) => string | undefined;
}) {
  const store = writtenAnswerStore(userId, question.id, question.version_id);
  const [draft, setDraft] = useState(() => store.read());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [needsVersion, setNeedsVersion] = useState(false);
  const requestRef = useRef<AbortController>();
  const pending = draft ? store.isPending(draft.requestId) : false;
  useEffect(() => () => requestRef.current?.abort(), []);

  async function submit() {
    if (!draft || !hasAnswer(draft.response, question.question_type) || requestRef.current || needsVersion) return;
    const request = new AbortController();
    requestRef.current = request;
    setBusy(true);
    setError("");
    store.markPending(draft.requestId);
    try {
      const receipt = await api.submitWrittenAttempt({
        question_id: question.id, version_id: question.version_id,
        request_id: draft.requestId, response: draft.response,
      }, request.signal);
      if (request.signal.aborted) return;
      store.markDone(draft.requestId);
      onSubmitted(receipt);
    } catch (failure) {
      if (request.signal.aborted) return;
      const stale = rejectionNeedsNewVersion(failure);
      setNeedsVersion(stale);
      if (stale || failure instanceof ApiError && failure.status === 422) store.markRejected(draft.requestId);
      setError(messageOf(failure));
    } finally {
      if (!request.signal.aborted) {
        requestRef.current = undefined;
        setBusy(false);
      }
    }
  }

  return <form onSubmit={(event) => { event.preventDefault(); void submit(); }} className="written-answer-form">
    <fieldset disabled={busy || needsVersion}>
      <legend>{question.question_type === "single_choice" ? "选择一个答案"
        : question.question_type === "multiple_choice" ? "选择所有正确答案" : "填写作答"}</legend>
      <WrittenAnswers question={question} response={draft?.response ?? null} imageSource={imageSource}
        onAnswer={(response) => { setDraft(store.write(response)); setError(""); }} />
    </fieldset>
    {error && <ErrorNotice message={error} />}
    {needsVersion && <button type="button" className="ghost" onClick={onCheckVersion}>查看最新题面</button>}
    {!needsVersion && <button className="btn-primary" type="submit" disabled={busy || !hasAnswer(draft?.response ?? null, question.question_type)}>
      {busy ? "正在提交…" : pending ? "重试提交" : "提交作答"}
    </button>}
  </form>;
}

function WrittenPaper({ question, receipt, userId, onSubmitted, onCheckVersion, onRetryQuestion }: {
  question: WrittenPublishedQuestion;
  receipt?: WrittenAttempt;
  userId: number;
  onSubmitted: (receipt: WrittenAttempt) => void;
  onCheckVersion: () => void;
  onRetryQuestion: () => void;
}) {
  const { files, loading, error, retry } = useWrittenImages(question);
  const imageSource = (path: string) => files.get(path);
  const usedMaterial = [
    question.shared_stem, question.stem, ...question.options.map((x) => x.text), receipt?.explanation,
  ].join("\n");
  const usedPaths = new Set([...usedMaterial.matchAll(/!\[[^\]\n]*\]\((assets\/[^)\n]+)\)/g)].map((hit) => hit[1]));
  const extraFigures = [...files].filter(([path]) => !usedPaths.has(path));
  return <div className="written-practice-paper">
    <article className="card written-paper-content">
      <div className="card-head"><h2>题目 #{question.id}</h2><span className="sub">CSP-{question.level} / {writtenTypeLabel[question.question_type]} / {question.score} 分</span></div>
      {question.shared_stem && <section className="written-shared"><h3>共用材料</h3>
        <Markdown text={question.shared_stem} imageSource={imageSource} />
      </section>}
      <Markdown text={question.stem} imageSource={imageSource} />
      {receipt && question.options.map((option) => <div key={option.id} className="written-option">
        <strong>{option.id}.</strong><Markdown text={option.text} imageSource={imageSource} />
      </div>)}
      {extraFigures.map(([path, url], index) => <figure key={path}>
        <img className="written-asset" src={url} alt={`题图 ${index + 1}`} />
      </figure>)}
      {loading && <p role="status" className="muted">正在加载题图…</p>}
      {error && <ErrorNotice message={error} onRetry={retry} />}
    </article>
    <section className="card written-paper-answer">
      {receipt ? <>
        <div className="card-head"><h2>作答结果</h2><WrittenScoreBadgeDirect receipt={receipt} /></div>
        <dl className="written-result-fields">
          <div><dt>我的答案</dt><dd>{answerText(receipt.response)}</dd></div>
          <div><dt>参考答案</dt><dd>{answerText(receipt.answer)}</dd></div>
        </dl>
        <h3>解析</h3>
        <Markdown text={receipt.explanation} imageSource={imageSource} />
        <p className="muted">版本 v{receipt.revision} / {new Date(receipt.submitted_at).toLocaleString("zh-CN")}</p>
        <button type="button" className="ghost" onClick={onRetryQuestion}>重新作答</button>
      </> : <>
        <h2>作答</h2>
        <SubmissionForm key={question.version_id} question={question} userId={userId} imageSource={imageSource}
          onSubmitted={onSubmitted} onCheckVersion={onCheckVersion} />
      </>}
    </section>
    {receipt && !receipt.correct && <WrittenHelp key={receipt.id} userId={userId} attemptId={receipt.id} />}
  </div>;
}

function WrittenScoreBadgeDirect({ receipt }: { receipt: WrittenAttempt }) {
  return <div className="written-result-score">
    <ScoreBadge correct={receipt.correct} />
    <strong>{receipt.earned_score} / {receipt.max_score} 分</strong>
  </div>;
}

type QuestionTarget = { id: number; version?: string };

function StaleAnswer({ userId, target, latest, saved, onUseLatest, onSubmitted }: {
  userId: number;
  target: QuestionTarget & { version: string };
  latest?: WrittenPublishedQuestion;
  saved: WrittenAnswerDraft;
  onUseLatest: (version: string) => void;
  onSubmitted: (receipt: WrittenAttempt) => void;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const requestRef = useRef<AbortController>();
  const store = writtenAnswerStore(userId, target.id, target.version);
  const pending = store.isPending(saved.requestId);
  useEffect(() => () => requestRef.current?.abort(), []);

  async function recover() {
    if (requestRef.current || !pending) return;
    const request = new AbortController();
    requestRef.current = request;
    setError("");
    setBusy(true);
    try {
      const receipt = await api.submitWrittenAttempt({
        question_id: target.id, version_id: target.version, request_id: saved.requestId, response: saved.response,
      }, request.signal);
      if (request.signal.aborted) return;
      store.markDone(saved.requestId);
      onSubmitted(receipt);
    } catch (failure) {
      if (request.signal.aborted) return;
      if (rejectionNeedsNewVersion(failure)) store.markRejected(saved.requestId);
      setError(messageOf(failure));
    } finally {
      if (!request.signal.aborted) {
        requestRef.current = undefined;
        setBusy(false);
      }
    }
  }

  return <section className="card">
    <h2>{latest ? "题目已有新版" : "题目已下架"}</h2>
    <p>旧版作答已保存在本机。</p>
    <dl className="written-result-fields"><div><dt>我的作答</dt><dd>{answerText(saved.response)}</dd></div></dl>
    {error && <ErrorNotice message={error} />}
    <div className="written-practice-actions">
      {pending && <button type="button" className="btn-primary" disabled={busy} onClick={() => void recover()}>
        {busy ? "正在核对…" : "核对上次提交"}
      </button>}
      {latest && <button type="button" className={pending ? "ghost" : "btn-primary"} disabled={busy}
        onClick={() => onUseLatest(latest.version_id)}>作答新版</button>}
    </div>
  </section>;
}

export function WrittenOpenQuestion({ userId, target, onVersion, onSubmitted }: {
  userId: number;
  target: QuestionTarget;
  onVersion: (version: string) => void;
  onSubmitted: (receipt: WrittenAttempt) => void;
}) {
  const [question, setQuestion] = useState<WrittenPublishedQuestion>();
  const [failure, setFailure] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const refresh = () => setReload((x) => x + 1);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setFailure(null);
    setQuestion(undefined);
    void api.writtenPublishedQuestion(target.id, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setQuestion(value); })
      .catch((error: unknown) => { if (!controller.signal.aborted) setFailure(error); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [target.id, reload]);
  const oldVersion = target.version;
  const changed = question && oldVersion !== question.version_id;
  const unavailable = failure instanceof ApiError && failure.status === 404;
  const oldDraft = oldVersion && (changed || unavailable) ? writtenAnswerStore(userId, target.id, oldVersion).read() : null;
  useEffect(() => {
    if (question && !oldDraft && oldVersion !== question.version_id) onVersion(question.version_id);
  }, [question, oldDraft, oldVersion, onVersion]);
  if (loading) return <p className="card" role="status">正在读取题面…</p>;
  if (oldVersion && oldDraft) return <StaleAnswer
    key={oldVersion} userId={userId} target={{ id: target.id, version: oldVersion }}
    latest={question} saved={oldDraft} onUseLatest={onVersion} onSubmitted={onSubmitted} />;
  if (failure) return <ErrorNotice message={messageOf(failure)} onRetry={refresh} />;
  if (!question) return null;
  return <WrittenPaper key={question.version_id} question={question} userId={userId}
    onSubmitted={onSubmitted} onCheckVersion={refresh} onRetryQuestion={refresh} />;
}

export function WrittenOpenAttempt({ userId, id, immediate, onRetryQuestion }: {
  userId: number;
  id: number;
  immediate?: WrittenAttempt;
  onRetryQuestion: (id: number) => void;
}) {
  const [receipt, setReceipt] = useState(immediate);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    if (immediate) return;
    const controller = new AbortController();
    setError("");
    void api.writtenAttempt(id, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setReceipt(value); })
      .catch((failure: unknown) => { if (!controller.signal.aborted) setError(messageOf(failure)); });
    return () => controller.abort();
  }, [id, immediate, reload]);
  if (error) return <ErrorNotice message={error} onRetry={() => setReload((x) => x + 1)} />;
  if (!receipt) return <p role="status" className="card">正在读取作答记录…</p>;
  return <WrittenPaper question={receipt.question} receipt={receipt} userId={userId}
    onSubmitted={() => {}} onCheckVersion={() => {}} onRetryQuestion={() => onRetryQuestion(receipt.question_id)} />;
}
