import { useEffect, useRef, useState } from "react";

export default function AccountCsvEditor({ value, onChange, onError, disabled, students = false, onReadingChange }: {
  value: string; onChange: (value: string) => void; onError: (message: string) => void; disabled: boolean; students?: boolean;
  onReadingChange?: (reading: boolean) => void;
}) {
  const generation = useRef(0);
  const [reading, setReading] = useState(false);
  useEffect(() => () => { generation.current++; }, []);
  return <div className="admin-form">
    <div><button type="button" className="ghost" disabled={disabled} onClick={() => {
      const url = URL.createObjectURL(new Blob(["\uFEFFusername,display_name,role,password,class_id\r\n"], { type: "text/csv;charset=utf-8" }));
      const link = document.createElement("a"); link.href = url; link.download = students ? "学生名单模板.csv" : "账号导入模板.csv"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    }}>下载 CSV 模板</button></div>
    <label>选择 CSV 文件（不超过 100 KB）<input type="file" accept=".csv,text/csv" disabled={disabled} onChange={async (event) => {
      const file = event.target.files?.[0]; event.target.value = ""; if (!file) return;
      const id = ++generation.current; onChange(""); setReading(false); onReadingChange?.(false);
      if (file.size > 100000) { onError("文件不得超过 100 KB，请减少本次导入人数。"); return; }
      setReading(true); onReadingChange?.(true);
      try { const text = await file.text(); if (id === generation.current) onChange(text); }
      catch { if (id === generation.current) onError("文件读取失败，请重新选择 UTF-8 CSV 文件。"); }
      finally { if (id === generation.current) { setReading(false); onReadingChange?.(false); } }
    }} /></label>
    {reading && <p role="status">正在读取文件…</p>}
    <label>CSV 内容<textarea rows={5} value={value} maxLength={100000} disabled={disabled || reading} spellCheck={false} onChange={(event) => { generation.current++; onChange(event.target.value); }} /></label>
    {students && <p className="muted">每行一名新学生，role 填 student，class_id 留空，创建时自动加入本班。已有账号请通过账号管理转班。最多 100 人；关闭窗口会清空名单和临时密码。</p>}
  </div>;
}
