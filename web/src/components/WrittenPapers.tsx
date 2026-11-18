import { useEffect, useRef, useState } from "react";
import { api, messageOf, type WrittenExam, type WrittenPaperQuestions, type WrittenPaperSummary, type WrittenPublishedSummary } from "../api";
import Modal from "./Modal";
import { ErrorNotice } from "./RecordUI";
import WrittenExamView, { WrittenExamHistory } from "./WrittenExam";
import { writtenTypeLabel } from "./WrittenPracticeQuestion";

const sourceLabels = { official_paper: "真题", simulation: "模拟题", original_demo: "自编演示" };

export default function WrittenPapers({ active, onPractice }: {
  active: boolean;
  onPractice: (question: WrittenPublishedSummary) => void;
}) {
  const [papers, setPapers] = useState<{ items: WrittenPaperSummary[]; has_more: boolean }>();
  const [selected, setSelected] = useState<WrittenPaperSummary>();
  const [detail, setDetail] = useState<WrittenPaperQuestions>();
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [history, setHistory] = useState(false);
  const [exam, setExam] = useState<WrittenExam>();
  const [confirm, setConfirm] = useState(false);
  const [examError, setExamError] = useState("");
  const [opening, setOpening] = useState(false);
  const examRequest = useRef<AbortController>();
  const refresh = () => setReload((value) => value + 1);
  const hasMore = selected ? detail?.has_more : papers?.has_more;

  useEffect(() => () => examRequest.current?.abort(), []);
  useEffect(() => {
    if (!active || history || exam) return;
    const request = new AbortController();
    setError("");
    setLoading(true);
    const load = selected
      ? api.writtenPaperQuestions(selected.batch_id, selected.id, offset, request.signal).then((value) => {
        if (!request.signal.aborted) setDetail(value);
      })
      : api.writtenPapers(offset, request.signal).then((value) => {
        if (!request.signal.aborted) setPapers(value);
      });
    void load.catch((failure) => { if (!request.signal.aborted) setError(messageOf(failure)); })
      .finally(() => { if (!request.signal.aborted) setLoading(false); });
    return () => request.abort();
  }, [active, offset, selected, reload, history, exam]);

  function select(paper?: WrittenPaperSummary) {
    setDetail(undefined);
    setOffset(0);
    setSelected(paper);
  }

  async function openExam(id?: number) {
    if (examRequest.current || (id === undefined && !detail)) return;
    const controller = new AbortController();
    examRequest.current = controller;
    setOpening(true);
    setExamError("");
    try {
      const result = id !== undefined ? await api.writtenExam(id, controller.signal)
        : await api.startWrittenExam(detail!.paper.batch_id, detail!.paper.id, controller.signal);
      if (controller.signal.aborted) return;
      setExam(result);
      setConfirm(false);
    } catch (failure) {
      if (!controller.signal.aborted) setExamError(messageOf(failure));
    } finally {
      if (!controller.signal.aborted) { examRequest.current = undefined; setOpening(false); }
    }
  }

  if (exam) return <div hidden={!active}><WrittenExamView key={exam.id} initial={exam} onBack={() => setExam(undefined)} /></div>;

  return <div hidden={!active}><section className="card written-practice-list">
    <div className="card-head">
      <h2>{history ? "模拟记录" : selected?.title ?? "按试卷练习"}</h2>
      {history ? <button className="ghost" type="button" disabled={opening} onClick={() => { setHistory(false); setExamError(""); }}>返回试卷</button>
        : <div className="written-practice-actions">
          {selected && <button className="ghost" type="button" onClick={() => select()}>返回试卷列表</button>}
          <button className="ghost" type="button" onClick={() => { setHistory(true); setExamError(""); }}>模拟记录</button>
        </div>}
    </div>
    {examError && !confirm && <ErrorNotice message={examError} />}
    {history && <WrittenExamHistory onOpen={(id) => void openExam(id)} />}
    {opening && !confirm && <p role="status">正在读取答卷…</p>}
    {!history && error && <ErrorNotice message={error} onRetry={refresh} />}
    {!history && loading && !error && <p role="status">正在读取试卷…</p>}
    {!history && !error && !loading && (selected && detail ? <>
      <p className="muted">
        已上架 {detail.paper.published_count} / 收录 {detail.paper.candidate_count} 题 · 上架题合计 {detail.paper.published_score} 分
        {detail.paper.source_total_score !== null && <> · 题包标注卷面 {detail.paper.source_total_score} 分</>}
      </p>
      {detail.paper.source_recorded_score !== null && detail.paper.source_total_score !== null
        && detail.paper.source_recorded_score !== detail.paper.source_total_score
        && <p className="muted">题包收录题合计 {detail.paper.source_recorded_score} 分，与标注卷面总分有差异。</p>}
      {detail.paper.duration_minutes !== null && <p className="muted">
        题包标注时长 {detail.paper.duration_minutes} 分钟
        {detail.paper.duration_source && <>（{detail.paper.duration_source}）</>}
      </p>}
      {detail.paper.mock_available && <button className="btn-primary" type="button" onClick={() => { setExamError(""); setConfirm(true); }}>打开限时模拟</button>}
      <div className="table-scroll"><table>
        <thead><tr><th>题号</th><th>题目</th><th>题型</th><th>分值</th><th>操作</th></tr></thead>
        <tbody>{detail.items.map((question) => <tr key={question.id}>
          <td>{question.number}</td><td><div className="written-excerpt">{question.stem}</div></td>
          <td>{writtenTypeLabel[question.question_type]}</td><td>{question.score}</td>
          <td><button className="ghost" type="button" onClick={() => onPractice(question)}>开始作答</button></td>
        </tr>)}</tbody>
      </table></div>
    </> : papers?.items.length ? <div className="table-scroll"><table>
      <thead><tr><th>试卷名称</th><th>级别</th><th>来源类型</th><th>已上架 / 收录</th><th>操作</th></tr></thead>
      <tbody>{papers.items.map((paper) => <tr key={`${paper.batch_id}:${paper.id}`}>
        <td>{paper.title}{paper.year && <small className="muted"> · {paper.year}</small>}</td>
        <td>{paper.level === "J" ? "J · 入门" : paper.level === "S" ? "S · 提高" : "未分级"}</td>
        <td>{paper.source_kind ? sourceLabels[paper.source_kind] : "未标注"}</td>
        <td>{paper.published_count} / {paper.candidate_count}</td>
        <td><button className="ghost" type="button" onClick={() => select(paper)}>查看试卷</button></td>
      </tr>)}</tbody>
    </table></div> : <div className="written-empty"><p>暂无包含上架题目的试卷。</p>
      <button type="button" className="ghost" onClick={refresh}>刷新试卷</button>
    </div>)}
    {!history && (offset > 0 || hasMore) && <div className="written-practice-actions">
      <button type="button" className="ghost" disabled={loading || offset === 0} onClick={() => setOffset(offset - 25)}>上一页</button>
      <button type="button" className="ghost" disabled={loading || !hasMore || !!error} onClick={() => setOffset(offset + 25)}>下一页</button>
    </div>}
    {confirm && detail && <Modal title="限时模拟" busy={opening} error={examError} onClose={() => setConfirm(false)}>
      <p>首次打开开始 {detail.paper.duration_minutes} 分钟计时。每套卷只保留一份答卷。离开后可在模拟记录继续，计时持续进行。</p>
      <button className="btn-primary" type="button" disabled={opening} onClick={() => void openExam()}>进入答卷</button>
    </Modal>}
  </section></div>;
}
