import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ErrorNotice } from "./RecordUI";

// 原生 modal dialog 提供背景隔离、焦点约束与关闭后的焦点恢复。
export default function Modal({ title, children, onClose, busy = false, wide = false, error = "" }: {
  title: string; children: ReactNode; onClose: () => void; busy?: boolean; wide?: boolean;
  error?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current!;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialog.showModal();
    dialog.querySelector<HTMLElement>("input:not([disabled]), select:not([disabled]), textarea:not([disabled])")?.focus();
    return () => {
      dialog.close(); document.body.style.overflow = overflow;
      queueMicrotask(() => {
        if (opener?.isConnected && !opener.matches(":disabled") && !document.querySelector("dialog[open]")) opener.focus();
      });
    };
  }, []);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  return createPortal(<dialog ref={ref} tabIndex={-1} className={`admin-modal${wide ? " admin-modal-wide" : ""}`} aria-labelledby={titleId}
    onKeyDown={(event) => {
      if (event.key !== "Tab") return;
      const items = Array.from(ref.current!.querySelectorAll<HTMLElement>('button, input, select, textarea, a[href], [tabindex]'))
        .filter((item) => !item.matches(':disabled, [tabindex="-1"], [type="hidden"]') && item.getClientRects().length > 0);
      const next = event.shiftKey ? items[items.length - 1] : items[0];
      if (!items.length || document.activeElement === (event.shiftKey ? items[0] : items[items.length - 1])) {
        event.preventDefault(); (next ?? ref.current)?.focus();
      }
    }}
    aria-busy={busy} onCancel={(event) => { event.preventDefault(); if (!busy) onClose(); }}>
    <header className="admin-modal-head"><h2 id={titleId}>{title}</h2><button type="button" className="ghost" disabled={busy} onClick={onClose} aria-label={`关闭${title}`}>关闭</button></header>
    <div className="admin-modal-body">{error && <div ref={errorRef} tabIndex={-1}><ErrorNotice message={error} /></div>}{busy && <p role="status">正在处理，请稍候…</p>}{children}</div>
  </dialog>, document.body);
}
