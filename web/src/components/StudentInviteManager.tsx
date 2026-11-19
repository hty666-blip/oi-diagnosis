import { useEffect, useRef, useState } from "react";
import { api, messageOf, type AdminClass, type StudentInvite } from "../api";
import Modal from "./Modal";
import { ErrorNotice } from "./RecordUI";

type InvitePage = { archived: boolean; items: StudentInvite[] };
const stateLabel = { active: "可使用", used: "已使用", expired: "已过期", revoked: "已撤销" };
const dateText = (value: string) => new Date(value).toLocaleString("zh-CN");

export default function StudentInviteManager({ group, onClose }: { group: AdminClass; onClose: () => void }) {
  const [page, setPage] = useState<InvitePage>();
  const [issued, setIssued] = useState<{ id: number; code: string }>();
  const [confirm, setConfirm] = useState<number>();
  const [notice, setNotice] = useState("");
  const [loadError, setLoadError] = useState("");
  const [writeError, setWriteError] = useState("");
  const [reload, setReload] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const write = useRef<AbortController>();

  useEffect(() => {
    const request = new AbortController();
    setLoadError("");
    void api.studentInvites(group.id, request.signal).then(
      (value) => { if (!request.signal.aborted) setPage(value); },
      (failure) => { if (!request.signal.aborted) setLoadError(messageOf(failure)); },
    ).finally(() => { if (!request.signal.aborted) setLoading(false); });
    return () => request.abort();
  }, [group.id, reload]);
  useEffect(() => () => write.current?.abort(), []);

  function refresh() {
    setLoading(true);
    setReload((value) => value + 1);
  }

  async function change(id?: number) {
    if (!page || loading || write.current) return;
    const request = new AbortController();
    write.current = request;
    setBusy(true);
    setWriteError("");
    setNotice("");
    try {
      if (id === undefined) {
        const { code, ...item } = await api.issueStudentInvite(group.id, request.signal);
        if (request.signal.aborted) return;
        setIssued({ id: item.id, code });
        setPage({ ...page, items: [item, ...page.items].slice(0, 25) });
      } else {
        const item = await api.revokeStudentInvite(group.id, id, request.signal);
        if (request.signal.aborted) return;
        setPage({ ...page, items: page.items.map((row) => row.id === id ? item : row) });
        if (issued?.id === id) setIssued(undefined);
        setConfirm(undefined);
      }
    } catch (failure) {
      if (!request.signal.aborted) setWriteError(messageOf(failure));
    } finally {
      if (!request.signal.aborted) { write.current = undefined; setBusy(false); }
    }
  }

  async function copy() {
    if (!issued) return;
    try {
      await navigator.clipboard.writeText(issued.code);
      setNotice("邀请码已复制。");
    } catch {
      setNotice("请选中邀请码并手动复制。");
    }
  }

  return <Modal title={`${group.name} · 学生邀请码`} wide onClose={onClose} busy={busy} error={writeError}>
    {loadError ? <ErrorNotice message={loadError} onRetry={refresh} />
      : !page ? <p role="status">正在读取邀请码…</p>
      : <>
        <div className="admin-actions">
          <button className="btn-primary" type="button" disabled={busy || loading || page.archived} onClick={() => void change()}>生成邀请码</button>
          <button className="ghost" type="button" disabled={busy || loading} onClick={refresh}>刷新状态</button>
        </div>
        {page.archived && <p className="muted">班级归档期间无法注册。</p>}
        {issued && <div className="admin-actions">
          <label>一次性邀请码<input readOnly value={issued.code} autoComplete="off" onFocus={(event) => event.currentTarget.select()} /></label>
          <button className="ghost" type="button" onClick={() => void copy()}>复制</button>
          <span className="muted">24 小时有效。关闭后无法再次查看原码。</span>
        </div>}
        {notice && <p role="status">{notice}</p>}
        {page.items.length === 0 ? <p className="muted">还没有签发邀请码。</p> : <div className="table-scroll"><table aria-label="最近 25 条邀请码">
          <thead><tr><th scope="col">编号</th><th scope="col">签发时间</th><th scope="col">到期时间</th><th scope="col">状态</th><th scope="col">操作</th></tr></thead>
          <tbody>{page.items.map((item) => <tr key={item.id}>
            <th scope="row">#{item.id}</th><td>{dateText(item.created_at)}</td><td>{dateText(item.expires_at)}</td>
            <td>{page.archived && item.state === "active" ? "班级已归档" : stateLabel[item.state]}</td>
            <td>{item.state === "active" && (confirm === item.id ? <div className="written-practice-actions">
              <button className="ghost" type="button" disabled={busy || loading} onClick={() => void change(item.id)}>确认撤销</button>
              <button className="ghost" type="button" disabled={busy} onClick={() => setConfirm(undefined)}>取消</button>
            </div> : <button className="ghost" type="button" disabled={busy || loading} onClick={() => setConfirm(item.id)}
              aria-label={`撤销邀请码 #${item.id}`}>撤销</button>)}</td>
          </tr>)}</tbody>
        </table></div>}
      </>}
  </Modal>;
}
