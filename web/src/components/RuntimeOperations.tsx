import { useEffect, useRef, useState } from "react";
import { api, messageOf, type RuntimeAI, type RuntimeBackups, type RuntimeJudging, type RuntimeWorkers } from "../api";
import { ErrorNotice } from "./RecordUI";
import Modal from "./Modal";

const time = (value: string | null) => value ? new Date(value).toLocaleString("zh-CN") : "尚无记录";
const states: Record<string, string> = { queued: "排队中", running: "执行中", failed: "失败", cancelled: "已取消", succeeded: "已完成" };
const errors: Record<string, string> = {
  verify_not_configured: "尚未配置独立恢复库，请联系部署人员。", verify_target_not_empty: "恢复验证库不是空库，请部署人员核对；现有数据未覆盖。",
  verify_target_unsafe: "恢复库配置不符合隔离要求。", verify_busy: "另一项恢复验证正在使用该库，请稍后重试。",
  archive_changed: "备份校验值不符，文件可能已损坏。", archive_missing: "本机备份文件已不可用。", decrypt_failed: "无法解密，请核对独立保存的备份密钥。",
  backup_key_unavailable: "备份密钥缺失或无效，请联系部署人员。", backup_too_large: "数据超过本机逻辑备份上限，请使用数据库备份工具。",
  permission_changed: "发起人的权限已变化，请管理员核对后重试。", attempts_exhausted: "已达到三次尝试上限，请排查后重新发起。",
  roundtrip_mismatch: "恢复后内容比对不一致。", schema_mismatch: "备份版本与当前程序不兼容。", version_integrity_failed: "题目版本完整性校验失败。",
  task_timeout: "任务执行超时，请检查数据库连接及磁盘后重试。", verify_cleanup_failed: "验证库清理失败，请部署人员检查后再使用。", restore_migration_failed: "恢复库结构初始化失败，请检查部署权限。",
};
const failure = (code: string) => errors[code] ?? (code.startsWith("http_") ? `模型服务返回 ${code.slice(5)}，请检查地址、模型及凭据。` : "本次未完成，请核对服务配置后重试。");

function Workers({ data, kind }: { data: RuntimeWorkers; kind: string }) {
  const items = data.items.filter((w) => w.kind === kind), online = items.filter((w) => w.online);
  return <div className="runtime-workers"><p><strong>{kind === "judge" ? "判题" : "备份"}工作器：{online.length ? `${online.length} 个在线` : "尚无有效在线心跳"}</strong></p>
    <p className="muted">读取于 {time(data.observed_at)}，超过 {data.stale_after_seconds} 秒的心跳不计在线。{items[0] ? `最近心跳 ${time(items[0].seen_at)}。` : "尚未收到心跳。"}</p>
    {online.map((w) => <p className="muted" key={w.id}>工作器 {w.id.slice(0, 8)} · {w.state === "busy" ? `正在处理 ${w.current_job ?? "任务"}` : "等待任务"}</p>)}</div>;
}

export default function RuntimeOperations() {
  const [tab, setTab] = useState<"judging" | "ai" | "backups">("judging");
  const [judging, setJudging] = useState<RuntimeJudging>(), [ai, setAI] = useState<RuntimeAI>(), [backups, setBackups] = useState<RuntimeBackups>();
  const [form, setForm] = useState<{ base_url: string; model: string; api_key: string }>();
  const [check, setCheck] = useState<Awaited<ReturnType<typeof api.runtimeAICheck>>>();
  const [query, setQuery] = useState(""), [filter, setFilter] = useState("all"), [offset, setOffset] = useState(0);
  const [language, setLanguage] = useState("cpp17"), [confirm, setConfirm] = useState<RuntimeJudging["items"][number]>();
  const [disable, setDisable] = useState(false), [busy, setBusy] = useState(false), [loading, setLoading] = useState(true);
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const generation = useRef(0), lock = useRef(false), requestIds = useRef(new Map<string, string>());

  async function load(quiet = false) {
    const id = ++generation.current;
    if (!quiet) { setLoading(true); setError(""); }
    try {
      if (tab === "judging") { const value = await api.runtimeJudging(filter, query, offset); if (id === generation.current) setJudging(value); }
      if (tab === "ai") { const value = await api.runtimeAI(); if (id === generation.current) { setAI(value); setForm((old) => old ?? { base_url: value.base_url, model: value.model, api_key: "" }); } }
      if (tab === "backups") { const value = await api.runtimeBackups(); if (id === generation.current) setBackups(value); }
    } catch (e) { if (id === generation.current) setError(messageOf(e)); }
    finally { if (id === generation.current && !quiet) setLoading(false); }
  }
  useEffect(() => { setConfirm(undefined); setError(""); setNotice(""); void load(); return () => { generation.current++; }; }, [tab, filter, offset]);
  useEffect(() => {
    if (tab !== "backups" || !backups?.tasks.some((t) => ["queued", "running"].includes(t.status))) return;
    const timer = setInterval(() => { if (!lock.current) void load(true); }, 3000);
    return () => clearInterval(timer);
  }, [tab, backups]);
  async function run(action: () => Promise<unknown>, message: string, onSuccess?: () => void) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(""); setNotice("");
    try { await action(); setNotice(message); await load(); setConfirm(undefined); onSuccess?.(); }
    catch (e) { setError(messageOf(e)); await load(true); }
    finally { lock.current = false; setBusy(false); }
  }
  async function enqueue(action: "create" | "verify", artifactId?: string) {
    const key = action + (artifactId ?? "");
    let id = requestIds.current.get(key);
    if (!id) { id = crypto.randomUUID(); requestIds.current.set(key, id); }
    await api.runtimeBackup(action, id, artifactId);
    requestIds.current.delete(key); // 网络失败继续复用同一编号，防止重复发起。
  }
  const blocked = busy || loading;
  return <div className="runtime-operations">
    <nav className="admin-tabs" aria-label="运行维护分类">{([["judging", "判题运维"], ["ai", "AI 服务"], ["backups", "备份与恢复验证"]] as const).map(([value, label]) => <button className="ghost" aria-pressed={tab === value} disabled={busy} key={value} onClick={() => setTab(value)}>{label}</button>)}<button className="ghost" disabled={blocked} onClick={() => void load()}>刷新运行状态</button></nav>
    {!confirm && !disable && <ErrorNotice message={error} onRetry={() => void load()} />}
    {notice && <p className="session-notice" role="status">{notice}</p>}{loading && <p role="status">正在读取运行状态…</p>}
    {tab === "judging" && judging && <>
      <section className="card"><h2>判题服务</h2><Workers data={judging.workers} kind="judge" /><p>执行方式：{judging.backend}。队列排队 {judging.summary.counts.queued} · 执行中 {judging.summary.counts.running} · 失败 {judging.summary.counts.failed}。</p>
        <p className="muted">编译并运行固定程序，检查所选语言。</p><div className="admin-actions"><label>检查语言<select value={language} onChange={(e) => setLanguage(e.target.value)} disabled={blocked}><option value="cpp17">C++17</option><option value="c11">C11</option><option value="java17">Java17</option><option value="python3">Python3</option></select></label><button className="ghost" disabled={blocked} onClick={() => void run(async () => { const result = await api.runtimeJudgeCheck(language); if (!result.ok) throw new Error("执行器检查未通过，请检查 Docker、镜像及运行权限后重试。"); }, "所选语言的编译和运行检查通过。")}>执行语言检查</button></div>
        <p className="muted">最近检查：{judging.last_check ? `${judging.last_check.language} · ${judging.last_check.ok ? "通过" : "未通过"} · ${time(judging.last_check.checked_at)}` : "尚未执行"}</p></section>
      <section className="card"><h2>判题任务</h2><form className="admin-actions" onSubmit={(e) => { e.preventDefault(); if (offset) setOffset(0); else void load(); }}><label>任务状态<select value={filter} onChange={(e) => { setFilter(e.target.value); setOffset(0); }} disabled={blocked}><option value="all">全部</option>{Object.entries(states).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label><label>题号或用户名<input value={query} maxLength={100} onChange={(e) => setQuery(e.target.value)} disabled={blocked} /></label><button className="ghost" disabled={blocked}>查询任务</button></form>
        <div className="table-scroll"><table><thead><tr><th>提交 / 学生</th><th>题目</th><th>执行情况</th><th>处理</th></tr></thead><tbody>{judging.items.map((r) => <tr key={r.submission_id}><td>#{r.submission_id} · {r.student}<small>{time(r.created_at)}</small></td><td>{r.pid} · {r.title}<small>{r.problem_version_id ? `固定版本 #${r.problem_version_id}` : "历史题目版本"}</small></td><td>{states[r.job.status] ?? r.job.status} · 尝试 {r.job.attempts}/{r.job.max_attempts}{r.job.last_error && <small>{r.job.last_error}</small>}</td><td>{r.protected ? "已完成或经教师审定" : r.job.retryable || r.job.cancelable ? <button className="ghost" disabled={blocked} onClick={() => { setError(""); setConfirm(r); }}>{r.job.retryable ? "重试此任务" : "取消此任务"}</button> : "无需操作"}</td></tr>)}</tbody></table></div>
        {!judging.items.length && <p className="muted">没有符合条件的任务。</p>}{confirm && <Modal title={confirm.job.retryable ? "重试判题任务" : "取消判题任务"} onClose={() => { setConfirm(undefined); setError(""); }} busy={busy} error={error}><p>{confirm.job.retryable ? `重新执行提交 #${confirm.submission_id} 的判题，保留原代码与固定题目版本。` : `取消提交 #${confirm.submission_id} 的执行，当前执行者返回的结果将不再写入。`}</p><div className="admin-actions"><button className="btn-primary" disabled={blocked} onClick={() => void run(() => api.runtimeJobAction(confirm.submission_id, confirm.job.retryable ? "retry" : "cancel", confirm.job), "任务操作已保存并记录审计。")}>确认{confirm.job.retryable ? "重试" : "取消"} #{confirm.submission_id}</button><button className="ghost" disabled={blocked} onClick={() => { setConfirm(undefined); setError(""); }}>暂不处理</button></div></Modal>}
        <div className="admin-actions"><button className="ghost" disabled={blocked || !offset} onClick={() => setOffset(Math.max(0, offset - 50))}>上一页任务</button><button className="ghost" disabled={blocked || !judging.has_more} onClick={() => setOffset(offset + 50)}>下一页任务</button></div></section>
    </>}
    {tab === "ai" && ai && form && <>
      <section className="card"><h2>AI 服务与配置</h2><p>当前{ai.enabled ? "允许调用" : "停止调用"} · {ai.configured ? "配置已齐全" : "配置尚未齐全"} · {ai.source === "environment" ? "使用部署配置" : `管理配置第 ${ai.version} 版`}。</p><p>当前模型：{ai.model || "未填写"}；服务地址：<span className="runtime-url">{ai.base_url || "未填写"}</span>。</p>
        {!ai.environment_allowed && <p className="muted">部署开关已关闭。请部署人员开启外部模型调用后再检查；管理员不能绕过部署开关。</p>}
        <p className="muted">先检查，再启用新配置。检查失败不替换现有配置；密钥不回显。</p>
        <form className="admin-form" onSubmit={(e) => { e.preventDefault(); void run(async () => { setCheck(undefined); const result = await api.runtimeAICheck({ ...form, expected_version: ai.version }); setForm({ ...form, api_key: "" }); setCheck(result); if (!result.ok) throw new Error(failure(result.error)); }, "候选配置检查通过，请核对后启用。检查在十分钟后失效。"); }}>
          <label>候选服务地址<input type="url" required value={form.base_url} maxLength={500} disabled={blocked} onChange={(e) => { setForm({ ...form, base_url: e.target.value }); setCheck(undefined); }} /></label>
          <label>候选模型名称<input required value={form.model} maxLength={100} disabled={blocked} onChange={(e) => { setForm({ ...form, model: e.target.value }); setCheck(undefined); }} /></label>
          <label>API 密钥<input type="password" autoComplete="new-password" value={form.api_key} maxLength={4096} placeholder={ai.key_configured ? "留空沿用现有密钥；换地址请重新填写" : "填写此模型服务的密钥"} disabled={blocked} onChange={(e) => { setForm({ ...form, api_key: e.target.value }); setCheck(undefined); }} /></label>
          <div className="admin-actions"><button className="ghost" disabled={blocked || !ai.environment_allowed}>检查候选配置</button>{check?.ok && <button className="btn-primary" type="button" disabled={blocked || Date.now() >= Date.parse(check.expires_at)} onClick={() => void run(async () => { const value = await api.runtimeAIActivate(check.check_id); setAI(value); setForm({ base_url: value.base_url, model: value.model, api_key: "" }); setCheck(undefined); }, "新配置已启用，后续答疑与讲评使用此配置。")}>启用已通过的配置</button>}</div>
        </form>{check?.ok && <p role="status">本次候选检查通过，耗时 {check.latency_ms ?? "未知"} ms，有效至 {time(check.expires_at)}。</p>}
        {ai.last_check && <p className="muted">最近候选检查：<span className="runtime-url">{ai.last_check.base_url}</span> · {ai.last_check.model} · {ai.last_check.ok ? "通过" : "未通过"} · {time(ai.last_check.created_at)}。候选检查通过不代表已启用。</p>}
        {ai.enabled && <div className="runtime-confirm">{disable ? <Modal title="停止外部 AI 调用" onClose={() => { setDisable(false); setError(""); }} busy={busy} error={error}><p>停止后，后续答疑与讲评使用规则降级；进行中的调用可能继续完成。</p><div className="admin-actions"><button className="ghost" disabled={blocked} onClick={() => void run(async () => { await api.runtimeAIDisable(ai.version); setCheck(undefined); }, "后续外部 AI 调用已停用。", () => setDisable(false))}>确认停止外部 AI</button><button className="ghost" disabled={blocked} onClick={() => { setDisable(false); setError(""); }}>保留启用</button></div></Modal> : <button className="ghost" disabled={blocked} onClick={() => { setError(""); setDisable(true); }}>停止外部 AI 调用</button>}</div>}</section>
      <section className="card"><h2>近期调用记录</h2><p>最近 {ai.usage.observed_records} 条记录中，外部调用 {ai.usage.external_calls} 次；已知输入 token {ai.usage.prompt_tokens ?? "未知"}，输出 token {ai.usage.completion_tokens ?? "未知"}；{ai.usage.unknown_usage} 次用量不完整。</p><p className="muted">诊断协作按 Agent 分别记录调用；答疑和讲评记录本次最终调用。不含网关重试的完整费用，实际账单以模型服务商为准。不保存学生源码、提示词、模型回复和密钥。</p>
        <div className="table-scroll"><table><thead><tr><th>时间 / 功能</th><th>模型</th><th>结果</th><th>耗时 / token</th></tr></thead><tbody>{ai.events.map((e) => <tr key={e.id}><td>{time(e.created_at)}<small>{{ tutor: "学习答疑", lesson: "训练讲评", diagnosis: "错题诊断", agent_evidence: "证据分析 Agent", agent_diagnosis: "错因诊断 Agent", agent_critic: "独立审查 Agent", check: "连通检查" }[e.feature] ?? e.feature}</small></td><td>{e.model || "规则模式"}</td><td>{e.used_model ? "采用模型结果" : e.called ? "未采用，已降级或检查失败" : "未调用模型"}{e.error && <small>{e.error.startsWith("http_") ? failure(e.error) : "规则降级 / 证据校验未通过"}</small>}</td><td>{e.latency_ms ?? "未知"} ms<small>输入 {e.prompt_tokens ?? "未知"} / 输出 {e.completion_tokens ?? "未知"}</small></td></tr>)}</tbody></table></div>{!ai.events.length && <p className="muted">尚无调用记录，下一次答疑、诊断或服务检查后再查看。</p>}</section>
    </>}
    {tab === "backups" && backups && <>
      <section className="card"><h2>加密备份与恢复验证</h2><Workers data={backups.workers} kind="maintenance" /><p>备份包含账号、班级、训练、提交、诊断和固定题目版本。模型密钥、部署环境文件及运行缓存不进入备份。</p><p className="muted">备份密钥由部署人员单独保管。下载文件仍为加密格式；请另做恢复验证。</p><p>{backups.verify_configured ? "独立恢复验证库已配置。验证会导入空库、逐表比对并清理验证数据，业务库保持运行。" : "独立恢复验证库尚未配置，请部署人员按运维文档设置后再验证。"}</p><button className="btn-primary" disabled={blocked} onClick={() => void run(() => enqueue("create"), "备份任务已排队，可离开本页后返回查看。")}>创建加密备份</button></section>
      <section className="card"><h2>最近备份</h2><div className="table-scroll"><table><thead><tr><th>创建时间 / 编号</th><th>内容</th><th>恢复验证</th><th>操作</th></tr></thead><tbody>{backups.items.map((a) => <tr key={a.id}><td>{time(a.created_at)}<small>{a.id.slice(0, 8)}</small></td><td>{a.size ? `${(a.size / 1024).toFixed(1)} KB · ${Object.values(a.counts).reduce((x, y) => x + y, 0)} 条` : "尚未生成文件"}<small>{a.revision || "等待执行"}</small>{a.sha256 && <details><summary>备份清单</summary><p className="runtime-url">SHA-256：{a.sha256}</p><p>记录：{Object.entries(a.counts).map(([name, count]) => `${name} ${count}`).join("；")}</p></details>}</td><td>{a.verified_at ? `已验证 · ${time(a.verified_at)}` : "尚未验证"}</td><td><div className="admin-actions"><button className="ghost" disabled={blocked || !a.size} onClick={() => void run(() => api.runtimeDownload(a.id), "加密文件已交给浏览器下载。")}>下载备份 {a.id.slice(0, 8)}</button><button className="ghost" disabled={blocked || !a.size || !backups.verify_configured || backups.tasks.some((t) => t.artifact_id === a.id && t.kind === "verify" && ["queued", "running"].includes(t.status))} onClick={() => void run(() => enqueue("verify", a.id), "恢复验证任务已创建，结果见下方任务记录。")}>验证恢复 {a.id.slice(0, 8)}</button></div></td></tr>)}</tbody></table></div>{!backups.items.length && <p className="muted">尚无备份。先创建一份，再执行恢复验证。</p>}</section>
      <section className="card"><h2>备份任务记录</h2><p className="muted">显示最近 50 个任务。执行中自动刷新；工作器重启后接管过期租约，最多尝试三次。</p>{backups.tasks.map((t) => <div className="runtime-task" key={t.id}><p><strong>{t.kind === "backup" ? "创建备份" : "恢复验证"}</strong> · {states[t.status] ?? t.status} · 尝试 {t.attempts}/3 · 备份 {t.artifact_id.slice(0, 8)}</p><p className="muted">发起于 {time(t.created_at)}{t.finished_at ? ` · 完成于 ${time(t.finished_at)}` : ""}</p>{t.status === "failed" && <p className="err">{failure(t.error)}</p>}{t.status === "failed" && t.attempts < 3 && <button className="ghost" disabled={blocked} onClick={() => void run(() => api.runtimeMaintenanceRetry(t.id), "任务已重新排队。")}>重试任务 {t.id.slice(0, 8)}</button>}{t.kind === "verify" && t.status === "succeeded" && <p role="status">逐表内容比对通过，旧登录已撤销，恢复后的 AI 保持关闭，验证数据已清理。</p>}</div>)}{!backups.tasks.length && <p className="muted">还没有备份任务。</p>}</section>
    </>}
  </div>;
}
