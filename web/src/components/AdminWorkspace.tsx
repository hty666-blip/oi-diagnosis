import { Component, lazy, Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { api, messageOf, type AccountInput, type AdminAccount, type AdminAudit, type AdminClass, type AdminOverview, type TransferImpact } from "../api";
import { parseAccountCsv, parseClassStudentCsv } from "../admin-import";
import { ErrorNotice } from "./RecordUI";

import Modal from "./Modal";
import AccountCsvEditor from "./AccountCsvEditor";

const RuntimeOperations = lazy(() => import("./RuntimeOperations"));
const ProblemCatalog = lazy(() => import("./ProblemCatalog"));
class CatalogBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <ErrorNotice message="管理页面未能加载，请刷新页面重试。" onRetry={() => window.location.reload()} /> : this.props.children; }
}
type Tab = "home" | "accounts" | "classes" | "audit" | "problems" | "runtime";
const roles = { student: "学生", teacher: "教练", admin: "管理员" };
const actions: Record<string, string> = { "ai.connectivity_check": "发起模型连通检查", "problem.import": "导入题目版本", "problem.state": "题目上下架", "account.create": "创建账号", "account.update": "更新账号", "account.reset_password": "重置密码", "account.change_password": "本人修改密码", "student.transfer": "学生转班", "class.create": "创建班级", "class.update": "更新班级 / 转交训练" };
const emptyAccount: AccountInput = { username: "", display_name: "", role: "student", password: "", class_id: null };

export default function AdminWorkspace({ onTeaching, onReview }: { onTeaching: () => void; onReview: () => void }) {
  const [tab, setTab] = useState<Tab>("home");
  const [overview, setOverview] = useState<AdminOverview>();
  const [classes, setClasses] = useState<AdminClass[]>([]);
  const [teachers, setTeachers] = useState<{ id: number; name: string; enabled: boolean }[]>([]);
  const [users, setUsers] = useState<AdminAccount[]>([]);
  const [more, setMore] = useState(false);
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [groupFilter, setGroupFilter] = useState<number>();
  const [audit, setAudit] = useState<AdminAudit[]>([]);
  const [auditMore, setAuditMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [creating, setCreating] = useState(false);
  const [account, setAccount] = useState<AccountInput>(emptyAccount);
  const [selected, setSelected] = useState<AdminAccount>();
  const [password, setPassword] = useState("");
  const [target, setTarget] = useState<number | null>(null);
  const [impact, setImpact] = useState<TransferImpact>();
  const [className, setClassName] = useState("");
  const [group, setGroup] = useState<AdminClass>();
  const [transferTo, setTransferTo] = useState<number | null>(null);
  const [csv, setCsv] = useState("");
  const [preview, setPreview] = useState<Awaited<ReturnType<typeof api.importPreview>>>();
  const [importing, setImporting] = useState(false);
  const [classCreating, setClassCreating] = useState(false);
  const [classTeachers, setClassTeachers] = useState<number[]>([]);
  const [classCsv, setClassCsv] = useState("");
  const [classReading, setClassReading] = useState(false);
  const [classPreview, setClassPreview] = useState<Awaited<ReturnType<typeof api.importPreview>>>();
  const generation = useRef(0), lock = useRef(false);

  async function load(nextOffset = offset, q = query, auditBefore?: number, filter = groupFilter) {
    const id = ++generation.current; setLoading(true); setError("");
    try {
      const [o, c, t, u, a] = await Promise.all([api.adminOverview(), api.adminClasses(), api.adminTeachers(), api.adminUsers(q, nextOffset, filter), api.adminAudit(auditBefore)]);
      if (id !== generation.current) return false;
      setOverview(o); setClasses(c); setTeachers(t); setUsers(u.items); setMore(u.has_more); setOffset(nextOffset); setAudit(a.items); setAuditMore(a.has_more);
      return true;
    } catch (e) { if (id === generation.current) setError(messageOf(e)); return false; }
    finally { if (id === generation.current) setLoading(false); }
  }
  useEffect(() => { void load(0); return () => { generation.current++; }; }, []);
  async function run(action: () => Promise<unknown>, message: string, onSuccess?: () => void) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(""); setNotice("");
    try { await action(); setNotice(message); const refreshed = await load(); setPassword(""); setSelected(undefined); setImpact(undefined); setGroup(undefined); setTransferTo(null); onSuccess?.(); if (!refreshed) setError("操作已保存，但列表刷新失败。请刷新列表，不要重复提交。"); }
    catch (e) { setError(messageOf(e)); }
    finally { lock.current = false; setBusy(false); }
  }
  const blocked = busy || loading;
  const activeClasses = classes.filter((c) => !c.archived);
  const teacherLabel = (id: number) => { const t = teachers.find((v) => v.id === id); return t ? `${t.name}${t.enabled ? "" : "（已停用）"}` : `原负责人 #${id}`; };
  function showMembers(id: number) { setGroupFilter(id); setQuery(""); setTab("accounts"); setSelected(undefined); setCreating(false); void load(0, "", undefined, id); }
  function openUser(row: AdminAccount) { setSelected(row); setPassword(""); setTarget(row.class_id ?? null); setImpact(undefined); setCreating(false); setError(""); }

  const modalOpen = creating || !!selected || importing || classCreating || !!group;
  function closeAccount() { setCreating(false); setAccount(emptyAccount); setError(""); }
  function closeUser() { setSelected(undefined); setPassword(""); setImpact(undefined); setError(""); }
  function closeImport() { setImporting(false); setCsv(""); setPreview(undefined); setError(""); }
  function closeClass() { setClassCreating(false); setClassName(""); setClassTeachers([]); setClassCsv(""); setClassReading(false); setClassPreview(undefined); setError(""); }
  function closeGroup() { setGroup(undefined); setTransferTo(null); setError(""); }
  async function previewClassStudents() {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(""); setClassPreview(undefined);
    try { setClassPreview(await api.importPreview(parseClassStudentCsv(classCsv))); }
    catch (e) { setError(messageOf(e)); }
    finally { lock.current = false; setBusy(false); }
  }

  return <div className="admin-workspace">
    <div className="page-head"><div><h1>平台管理</h1><p>管理账号、班级与负责关系，维护题库、AI 和备份，查看待处理事项和操作记录。</p></div><button className="ghost" onClick={onTeaching}>进入教学工作台</button></div>
    <nav className="admin-tabs" aria-label="管理功能">{([["home", "管理首页"], ["accounts", "账号管理"], ["classes", "班级管理"], ["problems", "题库管理"], ["runtime", "运行维护"], ["audit", "操作审计"]] as [Tab, string][]).map(([key, label]) => <button key={key} className="ghost" aria-pressed={tab === key} onClick={() => { setTab(key); setError(""); setNotice(""); }} disabled={busy}>{label}</button>)}{tab !== "problems" && tab !== "runtime" && <button className="ghost" onClick={() => void load()} disabled={blocked}>刷新</button>}</nav>
    {!modalOpen && <ErrorNotice message={error} onRetry={() => void load()} />}
    {notice && <p className="session-notice" role="status">{notice}</p>}
    {loading && <p role="status">正在读取管理记录…</p>}
    {tab === "problems" && <CatalogBoundary><Suspense fallback={<p role="status">正在加载题库管理…</p>}><ProblemCatalog /></Suspense></CatalogBoundary>}
    {tab === "runtime" && <CatalogBoundary><Suspense fallback={<p role="status">正在加载运行维护…</p>}><RuntimeOperations /></Suspense></CatalogBoundary>}
    {tab === "home" && overview && <>
      <section className="card"><div className="card-head"><h2>待处理事项</h2></div>
        <p>未分班的启用学生：{overview.unassigned_students} 人。<button className="ghost" onClick={() => showMembers(0)}>前往分班</button></p>
        {overview.classes_without_teacher.length ? <p>缺少启用教练：{overview.classes_without_teacher.map((c) => c.name).join("、")}。<button className="ghost" onClick={() => setTab("classes")}>分配教练</button></p> : <p className="muted">所有未归档班级均已分配启用的教练。</p>}
        <p>排队 {overview.judging.counts.queued} · 执行中 {overview.judging.counts.running} · 执行失败 {overview.judging.counts.failed}。<button className="ghost" onClick={onReview}>查看判题与复核</button><button className="ghost" onClick={() => setTab("runtime")}>处理运行故障</button></p>
      </section>
      <section className="card"><h2>当前可观测状态</h2><dl className="admin-status"><div><dt>数据库</dt><dd>本次查询已连接</dd></div><div><dt>判题工作器在线状态</dt><dd>{overview.worker_status === "online" ? "收到有效心跳，工作器在线" : "尚无有效心跳，请查看运行维护"}</dd></div><div><dt>外部 AI</dt><dd>{overview.llm_enabled ? "配置为启用；可用性需实际检查" : "配置为关闭"}</dd></div><div><dt>账号与班级</dt><dd>学生 {overview.accounts.student} · 教练 {overview.accounts.teacher} · 管理员 {overview.accounts.admin} · 停用 {overview.disabled} · 未归档班级 {overview.active_classes}</dd></div></dl><p className="muted">读取于 {new Date(overview.observed_at).toLocaleString("zh-CN")}。空队列不能证明工作器健康。</p></section>
      <section className="card"><div className="card-head"><h2>近期管理操作</h2><button className="ghost" onClick={() => setTab("audit")}>查看审计记录</button></div>{audit.slice(0, 5).map((r) => <p key={r.id}>{new Date(r.created_at).toLocaleString("zh-CN")} · {r.actor} · {actions[r.action] ?? r.action} · {r.target}</p>)}{!audit.length && <p className="muted">尚无管理操作。创建账号或班级后会在此留下记录。</p>}</section>
    </>}
    {tab === "accounts" && <>
      <section className="card"><div className="card-head"><h2>账号列表</h2><div className="admin-actions"><button className="ghost" disabled={blocked} onClick={() => { setError(""); setImporting(true); }}>批量导入账号</button><button className="btn-primary" disabled={blocked} onClick={() => { setError(""); setCreating(true); setSelected(undefined); setAccount(emptyAccount); }}>创建账号</button></div></div>
        <form className="admin-actions" onSubmit={(e) => { e.preventDefault(); void load(0); }}><label htmlFor="account-search">用户名或姓名</label><input id="account-search" value={query} maxLength={64} onChange={(e) => setQuery(e.target.value)} disabled={blocked} /><label>按班级筛选<select value={groupFilter ?? ""} onChange={(e) => setGroupFilter(e.target.value === "" ? undefined : Number(e.target.value))} disabled={blocked}><option value="">全部账号</option><option value="0">未分班学生</option>{classes.map((c) => <option key={c.id} value={c.id}>{c.name}{c.archived ? "（归档）" : ""}</option>)}</select></label><button className="ghost" disabled={blocked}>查询账号</button></form>
        <div className="table-scroll"><table><thead><tr><th>账号 / 姓名</th><th>角色</th><th>当前班级</th><th>状态</th><th>操作</th></tr></thead><tbody>{users.map((r) => <tr key={r.id}><td>{r.username}<div className="muted">{r.display_name}</div></td><td>{roles[r.role]}</td><td>{r.role === "student" ? r.class_name || "未分班" : "按负责关系管理"}</td><td>{r.enabled ? "启用" : "已停用"}{r.must_change_password && " · 待改密"}</td><td><button className="ghost" disabled={blocked} onClick={() => openUser(r)}>管理 {r.username}</button></td></tr>)}</tbody></table></div>
        {!users.length && !loading && <p className="muted">没有匹配的账号。可调整查询条件或创建账号。</p>}
        <div className="admin-actions"><button className="ghost" disabled={blocked || offset === 0} onClick={() => void load(Math.max(0, offset - 50))}>上一页账号</button><span>第 {Math.floor(offset / 50) + 1} 页 · 每页 50 条</span><button className="ghost" disabled={blocked || !more} onClick={() => void load(offset + 50)}>下一页账号</button></div>
      </section>
      {creating && <Modal title="创建账号" onClose={closeAccount} busy={busy} error={error}><p className="muted">新账号首次登录必须修改临时密码。用户名仅用字母、数字、点、下划线或短横线。</p><form className="admin-form" onSubmit={(e) => { e.preventDefault(); void run(async () => { await api.createAccount(account); }, "账号已创建。请通过可靠渠道告知临时密码。", closeAccount); }}>
        <label>用户名<input autoComplete="off" required maxLength={64} pattern="[a-zA-Z0-9][a-zA-Z0-9_.\-]*" value={account.username} onChange={(e) => setAccount({ ...account, username: e.target.value })} disabled={blocked} /></label>
        <label>姓名<input required maxLength={64} value={account.display_name} onChange={(e) => setAccount({ ...account, display_name: e.target.value })} disabled={blocked} /></label>
        <label>角色<select value={account.role} onChange={(e) => setAccount({ ...account, role: e.target.value as AccountInput["role"], class_id: null })} disabled={blocked}>{Object.entries(roles).map(([v, label]) => <option key={v} value={v}>{label}</option>)}</select></label>
        {account.role === "student" && <label>初始班级<select value={account.class_id ?? ""} onChange={(e) => setAccount({ ...account, class_id: e.target.value ? Number(e.target.value) : null })} disabled={blocked}><option value="">暂不分班</option>{activeClasses.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>}
        <label>临时密码<input type="password" autoComplete="new-password" required minLength={8} maxLength={128} value={account.password} onChange={(e) => setAccount({ ...account, password: e.target.value })} disabled={blocked} /></label>
        <div className="admin-actions"><button className="btn-primary" disabled={blocked}>确认创建</button><button className="ghost" type="button" onClick={closeAccount} disabled={blocked}>取消创建</button></div>
      </form></Modal>}
      {selected && <Modal title={`管理账号 · ${selected.username}`} onClose={closeUser} busy={busy} error={error}>
        <form className="admin-form" onSubmit={(e) => { e.preventDefault(); void run(() => api.editAccount(selected.id, { expected_version: selected.token_version, display_name: selected.display_name, role: selected.role, enabled: selected.enabled }), "账号设置已保存，原有登录已撤销。"); }}>
          <label>显示姓名<input required maxLength={64} value={selected.display_name} onChange={(e) => setSelected({ ...selected, display_name: e.target.value })} disabled={blocked} /></label>
          <label>账号角色<select value={selected.role} onChange={(e) => setSelected({ ...selected, role: e.target.value as AdminAccount["role"] })} disabled={blocked}>{Object.entries(roles).map(([v, label]) => <option key={v} value={v}>{label}</option>)}</select></label>
          <label>账号状态<select value={String(selected.enabled)} onChange={(e) => setSelected({ ...selected, enabled: e.target.value === "true" })} disabled={blocked}><option value="true">启用</option><option value="false">停用并撤销登录</option></select></label>
          <p className="muted">停用保留历史记录。修改角色前需先解除班级和训练负责关系。最后一个启用管理员受到保护。</p><button className="btn-primary" disabled={blocked}>保存账号设置</button>
        </form>
        <form className="admin-form admin-section" onSubmit={(e) => { e.preventDefault(); void run(() => api.resetPassword(selected.id, { expected_version: selected.token_version, password }), "临时密码已重置。旧登录已撤销，下次登录必须改密。"); }}><h3>重置密码</h3><label>新的临时密码<input type="password" required minLength={8} maxLength={128} autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} disabled={blocked} /></label><button className="ghost" disabled={blocked}>确认重置密码</button></form>
        {selected.role === "student" && <div className="admin-form admin-section"><h3>学生转班</h3><label>转入班级<select value={target ?? ""} onChange={(e) => { setTarget(e.target.value ? Number(e.target.value) : null); setImpact(undefined); }} disabled={blocked}><option value="">移出班级，暂不分班</option>{activeClasses.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label><button className="ghost" disabled={blocked || target === (selected.class_id ?? null)} onClick={async () => { if (lock.current) return; lock.current = true; setBusy(true); setError(""); try { setImpact(await api.transferPreview(selected.id, { expected_version: selected.token_version, class_id: target })); } catch (e) { setError(messageOf(e)); } finally { lock.current = false; setBusy(false); } }}>预览转班影响</button>
          {impact && <div role="status"><p>{impact.student}：{impact.from || "未分班"} → {impact.to}，影响 {impact.affected_trainings} 份旧班已发布训练。</p><p className="muted">{impact.notice}</p><button className="btn-primary" disabled={blocked} onClick={() => void run(() => api.transferStudent(selected.id, { expected_version: selected.token_version, class_id: target }), "转班已完成，旧登录已撤销；历史训练名单未改写。")}>确认转班</button></div>}
        </div>}
      </Modal>}
      {importing && <Modal title="批量导入账号" onClose={closeImport} busy={busy} error={error} wide><p className="muted">每次 1–100 行。班级填写班级管理页面中的编号，教练和管理员留空。临时密码仅用于创建，不保存到本机草稿。</p><AccountCsvEditor value={csv} onChange={(value) => { setCsv(value); setPreview(undefined); setError(""); }} onError={setError} disabled={blocked} />
        <button className="ghost" disabled={blocked || !csv} onClick={async () => { if (lock.current) return; lock.current = true; setBusy(true); setError(""); setPreview(undefined); try { setPreview(await api.importPreview(parseAccountCsv(csv))); } catch (e) { setError(messageOf(e)); } finally { lock.current = false; setBusy(false); } }}>预览导入</button>
        {preview && <><p role="status">共 {preview.rows.length} 行，{preview.errors.length} 处错误。{preview.errors.length ? "修正后重新预览。" : "确认后会重新校验，整批成功或整批不导入。"}</p>{preview.errors.map((e, i) => <p className="err" key={i}>数据第 {e.row} 行：{e.message}</p>)}<div className="table-scroll"><table><thead><tr><th>数据行</th><th>用户名 / 姓名</th><th>角色</th><th>班级编号</th></tr></thead><tbody>{preview.rows.map((r) => <tr key={r.row}><td>{r.row}</td><td>{r.username} / {r.display_name}</td><td>{roles[r.role as keyof typeof roles]}</td><td>{r.class_id ?? "未分班"}</td></tr>)}</tbody></table></div><button className="btn-primary" disabled={blocked || !!preview.errors.length} onClick={() => void run(async () => { await api.importAccounts(parseAccountCsv(csv)); }, "账号已整批导入，首次登录必须改密。", closeImport)}>确认导入 {preview.rows.length} 个账号</button></>}
      <div className="admin-actions"><button type="button" className="ghost" disabled={blocked} onClick={closeImport}>取消导入</button></div></Modal>}
    </>}
    {tab === "classes" && <>
      <section className="card"><div className="card-head"><h2>班级与负责教练</h2><button className="btn-primary" disabled={blocked} onClick={() => { closeClass(); setClassCreating(true); }}>创建班级</button></div><p className="muted">一个教练可负责多个班；学生只有一个当前班。归档停止新建和发布训练，已发布任务仍可完成。</p>

        <div className="table-scroll"><table><thead><tr><th>编号 / 班级</th><th>学生数</th><th>负责教练</th><th>状态</th><th>操作</th></tr></thead><tbody>{classes.map((c) => <tr key={c.id}><td>#{c.id} · {c.name}</td><td><button className="ghost" disabled={blocked} onClick={() => showMembers(c.id)}>查看 {c.students} 位学生</button></td><td>{c.teacher_ids.map(teacherLabel).join("、") || "尚未分配"}</td><td>{c.archived ? "已归档" : "使用中"}</td><td><button className="ghost" disabled={blocked} onClick={() => { setError(""); setGroup({ ...c, teacher_ids: [...c.teacher_ids] }); setTransferTo(null); }}>管理 {c.name}</button></td></tr>)}</tbody></table></div>{!classes.length && !loading && <p className="muted">先创建班级，再分配教练和学生。</p>}
      </section>
      {classCreating && <Modal title="创建班级" onClose={closeClass} busy={busy} error={error} wide>

        <p className="muted">填写班级信息，可同时分配负责教练并导入新学生。保存前不会创建任何账号。</p>
        <form className="admin-form" onSubmit={(event) => { event.preventDefault(); void run(async () => {
          if (classReading) throw new Error("学生名单正在读取，请稍候。");
          const students = classCsv.trim() ? parseClassStudentCsv(classCsv) : [];
          if (students.length && (!classPreview || classPreview.errors.length)) throw new Error("请先检查学生名单，修正错误后再创建。");
          await api.createClass(className.trim(), classTeachers, students);
        }, "班级、负责教练和学生名单已保存。新账号首次登录须修改临时密码。", closeClass); }}>
          <label>班级名称<input required maxLength={64} value={className} onChange={(event) => setClassName(event.target.value)} disabled={blocked} /></label>
          <fieldset disabled={blocked}><legend>负责教练（可多选，可稍后分配）</legend>{teachers.filter((t) => t.enabled).map((t) => <label className="admin-check" key={t.id}><input type="checkbox" checked={classTeachers.includes(t.id)} onChange={(event) => setClassTeachers(event.target.checked ? [...classTeachers, t.id] : classTeachers.filter((id) => id !== t.id))} />{t.name}</label>)}{!teachers.some((t) => t.enabled) && <p className="muted">暂无启用的教练，可先创建班级，再到账号管理创建教练并分配。</p>}</fieldset>
          <details className="admin-section"><summary>导入新学生（可选）{classCsv.trim() ? " · 已填写名单" : ""}</summary>
            <AccountCsvEditor value={classCsv} onChange={(value) => { setClassCsv(value); setClassPreview(undefined); setError(""); }} onError={setError} disabled={blocked} students onReadingChange={setClassReading} />
            <button type="button" className="ghost" disabled={blocked || !classCsv.trim()} onClick={() => void previewClassStudents()}>检查学生名单</button>
            {classPreview && <div role="status"><p>共 {classPreview.rows.length} 名学生，{classPreview.errors.length} 处错误。{classPreview.errors.length ? "请修正后重新检查。" : "名单可导入，创建时自动加入本班。"}</p>{classPreview.errors.map((item, index) => <p className="err" key={index}>数据第 {item.row} 行：{item.message}</p>)}<ul>{classPreview.rows.map((row) => <li key={row.row}>{row.username} · {row.display_name}</li>)}</ul></div>}
          </details>
          <div className="admin-actions"><button className="btn-primary" disabled={blocked || classReading || !className.trim() || (!!classCsv.trim() && (!classPreview || !!classPreview.errors.length))}>确认创建班级</button><button type="button" className="ghost" disabled={blocked} onClick={closeClass}>取消创建</button></div>
        </form>
      </Modal>}
      {group && <Modal title={`管理班级 · ${group.name}`} onClose={closeGroup} busy={busy} error={error}><form className="admin-form" onSubmit={(e) => { e.preventDefault(); void run(() => api.editClass(group.id, { name: group.name, expected_version: group.version, archived: group.archived, teacher_ids: group.teacher_ids, transfer_to: transferTo }), "班级设置已保存。改名保留归属关系，已发布名单不变。"); }}>
        <label>班级名称<input required maxLength={64} value={group.name} onChange={(e) => setGroup({ ...group, name: e.target.value })} disabled={blocked} /></label>
        <label>班级状态<select value={String(group.archived)} onChange={(e) => setGroup({ ...group, archived: e.target.value === "true" })} disabled={blocked}><option value="false">使用中</option><option value="true">归档，停止新建和发布训练</option></select></label>
        <fieldset disabled={blocked}><legend>负责教练（可多选）</legend>{teachers.map((t) => <label className="admin-check" key={t.id}><input type="checkbox" checked={group.teacher_ids.includes(t.id)} disabled={!t.enabled && !group.teacher_ids.includes(t.id)} onChange={(e) => { const ids = e.target.checked ? [...group.teacher_ids, t.id] : group.teacher_ids.filter((id) => id !== t.id); setGroup({ ...group, teacher_ids: ids }); if (transferTo === t.id) setTransferTo(null); }} />{teacherLabel(t.id)}</label>)}{!teachers.length && <p>请先创建教练账号。</p>}</fieldset>
        <h3>存量训练如何处理</h3><p className="muted">当前训练：{group.training_owners.map((o) => `${teacherLabel(o.teacher_id)} ${o.count} 份`).join("；") || "暂无"}。移除负责关系后，该教练将无法访问本班训练；也可明确转交给保留的负责教练。</p>
        <label>转交方案<select value={transferTo ?? ""} onChange={(e) => setTransferTo(e.target.value ? Number(e.target.value) : null)} disabled={blocked}><option value="">不转交，保留原归属供管理员管理</option>{group.teacher_ids.map((id) => <option key={id} value={id}>将非本班负责人的训练转交给 {teacherLabel(id)}</option>)}</select></label>
        <p className="muted">改名和转交不改写原始创建人、提交、讲评版本及学生发布名单。</p><div className="admin-actions"><button className="btn-primary" disabled={blocked}>保存班级设置</button><button className="ghost" type="button" disabled={blocked} onClick={closeGroup}>取消班级修改</button></div>
      </form></Modal>}
    </>}
    {tab === "audit" && <section className="card"><h2>操作审计</h2><p className="muted">账号、转班、教练负责关系及训练转交与业务变更一起保存。密码原文和哈希不进入此记录。</p>{audit.map((r) => <details className="admin-audit" key={r.id}><summary>{new Date(r.created_at).toLocaleString("zh-CN")} · {r.actor} · {actions[r.action] ?? r.action} · {r.target}</summary><div className="admin-audit-values"><div><h3>变更前</h3><pre>{JSON.stringify(r.before, null, 2)}</pre></div><div><h3>变更后</h3><pre>{JSON.stringify(r.after, null, 2)}</pre></div></div></details>)}{!audit.length && !loading && <p>暂无操作记录。</p>}<div className="admin-actions"><button className="ghost" disabled={blocked} onClick={() => void load()}>最新审计</button><button className="ghost" disabled={blocked || !auditMore} onClick={() => void load(offset, query, audit[audit.length - 1]?.id)}>更早审计</button></div></section>}
  </div>;
}
