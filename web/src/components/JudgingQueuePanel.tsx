import { useEffect, useState } from "react";
import { api, messageOf, type JudgingQueue } from "../api";
import { ErrorNotice } from "./RecordUI";

function observedTime(value: string | null): string {
  if (!value) return "未记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "未记录" : date.toLocaleString("zh-CN");
}

function waitLabel(seconds: number | null): string {
  if (seconds === null) return "当前没有排队任务";
  if (!Number.isFinite(seconds) || seconds < 0) return "未记录";
  if (seconds < 1) return "不足 1 秒";
  if (seconds < 60) return Math.floor(seconds) + " 秒";
  return Math.floor(seconds / 60) + " 分 " + Math.floor(seconds % 60) + " 秒";
}

export default function JudgingQueuePanel() {
  const [snapshot, setSnapshot] = useState<JudgingQueue | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setLoading(true); setError("");
    api.coachJudging(controller.signal).then((value) => { if (active) setSnapshot(value); })
      .catch((e) => { if (active) setError(messageOf(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [refresh]);

  const scopeLabel = snapshot?.scope.kind === "all" ? "全部授权记录"
    : snapshot?.scope.kind === "class_and_own" ? (snapshot.scope.class_name || "本班") + "与本人记录" : "本人记录";
  return <section className="card judging-queue" aria-label="判题队列状态">
    <div className="card-head"><h2>判题队列</h2><button className="ghost" disabled={loading} onClick={() => setRefresh((value) => value + 1)}>{loading ? "正在读取…" : "刷新判题状态"}</button></div>
    <ErrorNotice message={error ? "判题状态未更新：" + error : ""} onRetry={() => setRefresh((value) => value + 1)} />
    {!snapshot && loading && <p className="muted" role="status">正在读取授权范围内的判题记录…</p>}
    {snapshot && <>
      <p className="dim">{scopeLabel} · 记录时间：{observedTime(snapshot.observed_at)}{loading ? " · 正在更新" : error ? " · 以下为上次成功读取的数据" : ""}</p>
      <dl className="judging-counts">{([
        ["queued", "排队"], ["running", "执行中"], ["failed", "失败"], ["cancelled", "已取消"], ["succeeded", "完成"],
      ] as const).map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{snapshot.counts[key]}</dd></div>)}</dl>
      <p className="muted">最久可执行后等待：{waitLabel(snapshot.oldest_queued_wait_seconds)}。不含尚未到期的重试退避。</p>
      {snapshot.counts.running > 0 && <p className="dim">执行租约记录：有效 {snapshot.leases.active} · 已过期 {snapshot.leases.expired} · 缺失 {snapshot.leases.missing}。{snapshot.leases.expired + snapshot.leases.missing > 0 ? "存在需检查恢复情况的执行记录。" : ""}这些记录不能用于判断执行服务是否在线。</p>}
      <details className="judging-failures"><summary>最近失败记录（最多 5 条）{snapshot.recent_failures.length ? " · " + snapshot.recent_failures.length + " 条" : ""}</summary>
        {!snapshot.recent_failures.length ? <p className="muted">授权范围内暂无失败记录。</p> : <ul>{snapshot.recent_failures.map((failure) => <li key={failure.submission_id}>
          <strong>#{failure.submission_id} · {failure.student_name} · {failure.problem_pid} {failure.problem_title}</strong>
          <p>{failure.last_error}</p><span className="dim">已尝试 {failure.attempts} 次 · 结束时间：{observedTime(failure.finished_at)}</span>
        </li>)}</ul>}
      </details>
    </>}
  </section>;
}
