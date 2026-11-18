import { useEffect, useRef, useState } from "react";
import { api, messageOf, type WrittenShortReview as Review } from "../api";
import Markdown from "../Markdown";
import Modal from "./Modal";
import { ErrorNotice } from "./RecordUI";
import { useWrittenImages } from "./WrittenPracticeQuestion";

export default function WrittenShortReview({ assignmentId, attemptId, onClose, onConfirmed }: {
  assignmentId: number; attemptId: number; onClose: () => void; onConfirmed: () => void;
}) {
  const [review, setReview] = useState<Review>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [suggesting, setSuggesting] = useState(false);
  const [suggestError, setSuggestError] = useState("");
  const [reload, setReload] = useState(0);
  const confirmRequest = useRef<AbortController>();
  const suggestRequest = useRef<AbortController>();
  useEffect(() => () => {
    confirmRequest.current?.abort();
    suggestRequest.current?.abort();
  }, []);
  useEffect(() => {
    const request = new AbortController();
    setError("");
    void api.writtenShortReview(assignmentId, attemptId, request.signal)
      .then((value) => { if (!request.signal.aborted) setReview(value); })
      .catch((failure) => { if (!request.signal.aborted) setError(messageOf(failure)); });
    return () => request.abort();
  }, [assignmentId, attemptId, reload]);

  async function confirm(body: { points: number[]; feedback: string }) {
    if (confirmRequest.current) return;
    const request = new AbortController();
    confirmRequest.current = request;
    setError(""); setBusy(true);
    try {
      await api.confirmWrittenShortReview(assignmentId, attemptId, body, request.signal);
      if (!request.signal.aborted) onConfirmed();
    } catch (failure) {
      if (!request.signal.aborted) setError(messageOf(failure));
    } finally {
      confirmRequest.current = undefined;
      if (!request.signal.aborted) setBusy(false);
    }
  }

  async function suggest() {
    if (suggestRequest.current) return;
    const request = new AbortController();
    suggestRequest.current = request;
    setSuggesting(true); setSuggestError("");
    try {
      const result = await api.suggestWrittenShortReview(assignmentId, attemptId, request.signal);
      if (!request.signal.aborted) setReview((value) => value && { ...value, suggestion: result });
    } catch (failure) {
      if (!request.signal.aborted) setSuggestError(messageOf(failure));
    } finally {
      suggestRequest.current = undefined;
      if (!request.signal.aborted) setSuggesting(false);
    }
  }

  return <Modal title="复核简答" busy={busy} onClose={onClose}>
    {error && <ErrorNotice message={error} onRetry={review ? undefined : () => setReload((value) => value + 1)} />}
    {review ? <ReviewForm review={review} busy={busy} suggesting={suggesting}
      suggestError={suggestError} onSuggest={() => void suggest()} onConfirm={(body) => void confirm(body)} />
      : !error && <p role="status">正在读取原作答和评分要点…</p>}
  </Modal>;
}

function ReviewForm({ review, busy, suggesting, suggestError, onSuggest, onConfirm }: {
  review: Review; busy: boolean; suggesting: boolean; suggestError: string;
  onSuggest: () => void; onConfirm: (body: { points: number[]; feedback: string }) => void;
}) {
  const [points, setPoints] = useState(review.answer.rubric.map((_, index) => review.grade?.points[index]?.toString() ?? ""));
  const [feedback, setFeedback] = useState(review.grade?.feedback ?? "");
  const [confirming, setConfirming] = useState(false);
  const images = useWrittenImages(review.question);
  const imageSource = (path: string) => images.files.get(path);
  const material = review.question.shared_stem + "\n" + review.question.stem;
  const shown = new Set([...material.matchAll(/!\[[^\]\n]*\]\((assets\/[^)\n]+)\)/g)].map((hit) => hit[1]));
  const total = points.reduce((sum, value) => sum + Number(value), 0);
  const valid = points.every((value, i) => /^\d+$/.test(value) && Number(value) <= review.answer.rubric[i].points)
    && feedback.trim().length >= 3;
  return <form className="written-review-fields" onSubmit={(event) => {
    event.preventDefault();
    if (!busy && valid && !review.grade && !review.read_only) {
      if (!confirming) setConfirming(true);
      else onConfirm({ points: points.map(Number), feedback });
    }
  }}>
    <div>
      <h3>提交时的题面 v{review.question.revision}</h3>
      {review.question.shared_stem && <Markdown text={review.question.shared_stem} imageSource={imageSource} />}
      <Markdown text={review.question.stem} imageSource={imageSource} />
      {[...images.files].filter(([path]) => !shown.has(path)).map(([path, source], i) =>
        <figure key={path}><img className="written-asset" src={source} alt={`题图 ${i + 1}`} /></figure>)}
      {images.loading && <p role="status">正在读取题图…</p>}
      {images.error && <ErrorNotice message={images.error} onRetry={images.retry} />}
    </div>
    <div><h3>学生作答</h3><p style={{ whiteSpace: "pre-wrap" }}>{review.response}</p></div>
    <div><h3>参考答案</h3><p style={{ whiteSpace: "pre-wrap" }}>{review.answer.reference}</p></div>
    {!review.grade && !review.read_only && review.can_suggest && <section className="scope-note">
      <h3>AI 草评</h3>
      {review.suggestion ? <>
        <ol>{review.suggestion.awards.map((award, i) => <li key={i}>
          {review.answer.rubric[i].criterion} · 建议 {award.points} 分。{award.reason}
          {award.quote && <blockquote>{award.quote}</blockquote>}
        </li>)}</ol>
        <p>复核 Agent：{review.suggestion.review_note}</p>
        <button className="ghost" type="button" disabled={busy || confirming}
          onClick={() => setPoints(review.suggestion!.awards.map((award) => String(award.points)))}>
          将建议分填入评分表
        </button>
      </> : <>
        <p>将题面、评分要点和本次作答发送给平台已配置的模型。教师可直接人工给分。</p>
        <button className="ghost" type="button" disabled={busy || confirming || suggesting} onClick={onSuggest}>
          {suggesting ? "草评与复核中…" : "生成 AI 草评"}
        </button>
      </>}
      {suggestError && <ErrorNotice message={suggestError} />}
    </section>}
    <fieldset className="written-review-fields" disabled={busy || !!review.grade || review.read_only || confirming}>
      <legend>逐项给分</legend>
      {review.answer.rubric.map((item, i) => <label key={i}>{item.criterion}（满分 {item.points}）
        <input aria-label={`第 ${i + 1} 项得分`} type="number" min={0} max={item.points} step={1}
          value={points[i]} onChange={(event) => setPoints((old) => old.map((value, n) => n === i ? event.target.value : value))} />
      </label>)}
      <label>给学生的反馈<textarea rows={4} value={feedback} maxLength={3000} onChange={(e) => setFeedback(e.target.value)} /></label>
    </fieldset>
    <div className="admin-actions">
      <strong>{total} / {review.question.score} 分</strong>
      {review.grade ? <span className="badge route">已发布</span> : !review.read_only && <>
        {confirming && <button type="button" className="ghost" disabled={busy} onClick={() => setConfirming(false)}>返回编辑</button>}
        <button type="submit" className="btn-primary" disabled={busy || !valid || images.loading || !!images.error}>
          {busy ? "正在发布…" : confirming ? "确认发布成绩" : "预览成绩"}
        </button>
      </>}
    </div>
    {confirming && <p role="status">发布后该学生可查看分数和反馈，本次评分将锁定。</p>}
    {review.read_only && !review.grade && <p>班级已归档，评分不可编辑。</p>}
  </form>;
}
