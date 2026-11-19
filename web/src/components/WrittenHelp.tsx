import { useEffect, useRef, useState } from "react";
import {
  api, ApiError, messageOf, readLocal, removeLocal, writeLocal,
  type WrittenHelpHistory, type WrittenHelpReference, type WrittenHelpTarget, type WrittenHelpTurn,
} from "../api";
import { shouldSendMessage } from "../editor-input";
import Markdown from "../Markdown";
import { emptyQuestionDraft, questionDraftStore, type QuestionDraft } from "../question-draft";
import { ErrorNotice } from "./RecordUI";

const starter = "请讲一下我这道题为什么答错。";
const pendingPollMs = 2000;
const storage = { getItem: readLocal, setItem: writeLocal, removeItem: removeLocal };
const references: Record<WrittenHelpReference, string> = {
  attempt: "本次作答", review: "审核答案与解析", knowledge: "知识卡", prior_practice: "同知识点作答",
};
const reasons: Record<string, string> = {
  model_disabled: "平台 AI 已关闭", model_unconfigured: "平台 AI 尚未配置",
  model_unavailable: "模型暂不可用", model_timeout: "模型响应超时",
  model_output_invalid: "模型给出的依据未通过校验",
};

function DiscussionTurn({ turn }: { turn: WrittenHelpTurn }) {
  const diagnosis = turn.diagnosis;
  const tutor = turn.tutor;
  return <article className="written-help-turn">
    <div className="bubble student"><div className="lvl">我的问题</div><p>{turn.question}</p></div>
    <div className="bubble tutor">
      <div className="lvl">{tutor.source === "model" ? "答疑 Agent" : "审核解析讲解"}</div>
      <Markdown text={tutor.reply} />
      <details className="written-help-evidence">
        <summary>查看诊断交接与依据</summary>
        <p>{turn.comparison}</p>
        <dl className="written-result-fields">
          <div><dt>诊断 Agent</dt><dd>
            {diagnosis.source === "model" ? "已生成交接" : reasons[diagnosis.reason] ?? "采用规则比较"}
            {diagnosis.hypothesis && <p>可能原因：{diagnosis.hypothesis}</p>}
            <p>{diagnosis.focus}</p>
            <small>{diagnosis.references.map((key) => references[key]).join("、")}</small>
          </dd></div>
          <div><dt>答疑 Agent</dt><dd>
            {tutor.source === "model" ? "已根据交接生成讲解" : reasons[tutor.reason] ?? "采用审核解析"}
            <p>{tutor.references.map((key) => references[key]).join("、")}</p>
          </dd></div>
        </dl>
        {Boolean(turn.prior_practice?.length) && <section className="written-help-knowledge">
          <h3>此前同知识点作答</h3>
          <ul>{turn.prior_practice?.map((item) => <li key={item.attempt_id}>
            题目 #{item.question_id} · v{item.revision} · {item.correct ? "答对" : "答错"}
          </li>)}</ul>
        </section>}
        {turn.knowledge && <section className="written-help-knowledge">
          <h3>{turn.knowledge.title} <small>v{turn.knowledge.revision}</small></h3>
          <Markdown text={turn.knowledge.body} />
          {turn.knowledge.excerpt && <p className="muted">本次引用正文节选。</p>}
          {turn.knowledge.source && <p className="muted">来源　{turn.knowledge.source}</p>}
        </section>}
      </details>
    </div>
  </article>;
}

export default function WrittenHelp({ userId, target }: { userId: number; target: WrittenHelpTarget }) {
  const key = typeof target === "number" ? target : `${target.examId}:${target.questionId}`;
  const store = questionDraftStore(storage, userId, key, undefined, typeof target === "number" ? "written" : "written-exam");
  const [draft, setDraft] = useState(() => store.read());
  const [history, setHistory] = useState<WrittenHelpHistory>({ items: [], before_id: null, pending: null });
  const [hydrated, setHydrated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [clock, setClock] = useState(Date.now);
  const readRequest = useRef<AbortController>();
  const sendRequest = useRef<AbortController>();
  const textarea = useRef<HTMLTextAreaElement>(null);

  async function load(before?: number, background = false) {
    if (background && readRequest.current) return;
    readRequest.current?.abort();
    const request = new AbortController();
    readRequest.current = request;
    if (!background) setLoading(true);
    try {
      const value = await api.writtenHelpHistory(target, before, request.signal);
      if (request.signal.aborted) return;
      if (before) {
        setHistory((current) => ({ ...current, before_id: value.before_id, items: [...value.items, ...current.items] }));
      } else {
        setHistory((current) => {
          if (!background || !value.items.length) return value;
          const earlier = current.items.filter((item) => item.id < value.items[0].id);
          return earlier.length ? { ...value, items: [...earlier, ...value.items], before_id: current.before_id } : value;
        });
        if (background && !value.pending) setError("");
        for (const turn of value.items) store.acknowledge(turn.request_id);
        setDraft((current) => value.items.some((turn) => turn.request_id === current.id) ? emptyQuestionDraft : current);
      }
      setHydrated(true);
    } catch (failure) {
      if (!request.signal.aborted && !background) setError(messageOf(failure));
    } finally {
      if (!request.signal.aborted) {
        readRequest.current = undefined;
        setClock(Date.now());
        if (!background) setLoading(false);
      }
    }
  }

  useEffect(() => {
    void load();
    return () => {
      readRequest.current?.abort();
      sendRequest.current?.abort();
    };
  }, [userId, key]);

  async function ask(value: QuestionDraft) {
    if (sendRequest.current || loading || !value.content.trim() || !value.id) return;
    readRequest.current?.abort();
    readRequest.current = undefined;
    const request = new AbortController();
    sendRequest.current = request;
    setSending(true);
    setError("");
    try {
      const prior = history.items[history.items.length - 1];
      const turn = await api.askWrittenHelp(target, {
        request_id: value.id, message: value.content, after_id: prior?.id ?? null,
      }, request.signal);
      if (request.signal.aborted) return;
      store.acknowledge(value.id);
      setDraft((current) => current.id === value.id ? emptyQuestionDraft : current);
      if (turn.replayed) await load();
      else {
        setHistory((current) => ({
          ...current, pending: null,
          items: current.items.some((item) => item.id === turn.id) ? current.items : [...current.items, turn],
        }));
      }
      textarea.current?.focus();
    } catch (failure) {
      if (request.signal.aborted) return;
      setError(messageOf(failure));
      if (failure instanceof ApiError && failure.status === 409) await load();
    } finally {
      if (!request.signal.aborted) {
        sendRequest.current = undefined;
        setSending(false);
      }
    }
  }

  function start() {
    if (sendRequest.current) return;
    const value = store.write(starter);
    setDraft(value);
    void ask(value);
  }

  const pending = history.pending;
  const pendingId = pending?.request_id;
  const retryAt = pending ? Date.parse(pending.retry_at) : 0;
  const waiting = Boolean(pending && retryAt > clock);
  const composerDraft = draft.id === pendingId ? emptyQuestionDraft : draft;

  useEffect(() => {
    if (!pendingId || !waiting || loading || sending) return;
    const timer = window.setInterval(() => {
      setClock(Date.now());
      void load(undefined, true);
    }, pendingPollMs);
    return () => window.clearInterval(timer);
  }, [key, pendingId, waiting, loading, sending]);

  const disabled = loading || sending || !hydrated;
  const sendDisabled = disabled || waiting;
  return <section className="card written-help" aria-label="错题答疑">
    <div className="card-head"><h2>错题答疑</h2>
      <button type="button" className="ghost" disabled={loading || sending}
        onClick={() => { setError(""); void load(); }}>刷新记录</button>
    </div>
    {loading && <p role="status" className="muted">正在读取答疑记录…</p>}
    {error && <ErrorNotice message={error} />}
    {history.before_id && <button type="button" className="ghost" disabled={disabled}
      onClick={() => void load(history.before_id ?? undefined)}>更早的对话</button>}
    {history.items.length > 0 && <div role="log" aria-label="答疑记录" className="written-help-log">
      {history.items.map((turn) => <DiscussionTurn key={turn.id} turn={turn} />)}
    </div>}
    {pending && <div className="written-help-pending">
      <p role="status">{waiting ? "正在生成讲解，结果会自动出现。" : "上次追问尚未完成。"}</p>
      <p className="muted">{pending.message}</p>
      {!waiting && <button type="button" className="ghost" disabled={disabled}
        onClick={() => void ask({ id: pending.request_id, content: pending.message })}>继续上次追问</button>}
    </div>}
    {sending && <p role="status">正在分析作答并准备讲解…</p>}
    {hydrated && !history.items.length && !pending && !draft.content && <button type="button"
      className="btn-primary" disabled={disabled} onClick={start}>讲解这道错题</button>}
    {hydrated && <form className="composer written-help-composer" onSubmit={(event) => {
      event.preventDefault();
      if (!sendDisabled) void ask(composerDraft);
    }}>
      <label>追问
        <textarea ref={textarea} rows={2} value={composerDraft.content} maxLength={1200} disabled={sending}
          onChange={(event) => { setDraft(store.write(event.target.value)); setError(""); }}
          onKeyDown={(event) => {
            if (shouldSendMessage(event.nativeEvent)) {
              event.preventDefault();
              if (!sendDisabled) void ask(composerDraft);
            }
          }} />
      </label>
      <button type="submit" className="btn-primary" disabled={sendDisabled || !composerDraft.content.trim()}>发送问题</button>
    </form>}
  </section>;
}
