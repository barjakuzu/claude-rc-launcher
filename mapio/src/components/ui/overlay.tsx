"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";

/** Accessible modal on the native <dialog> (focus trap, Esc, backdrop) with glass styling. */
export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
      aria-labelledby="modal-title"
      className="glass-elevated glass-accent m-auto w-[min(480px,calc(100vw-2rem))] p-0 text-text backdrop:bg-[#020611]/70 backdrop:backdrop-blur-sm open:animate-[rise_240ms_var(--ease)_both]"
    >
      <div className="p-6">
        <div className="flex items-start justify-between gap-4">
          <h2 id="modal-title" className="text-xl font-semibold tracking-tight">
            {title}
          </h2>
          <button onClick={onClose} aria-label="Close" className="-m-1 grid h-9 w-9 place-items-center rounded-xl text-muted hover:text-text">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="mt-3 text-muted">{children}</div>
        {footer && <div className="mt-6 flex flex-wrap justify-end gap-2">{footer}</div>}
      </div>
    </dialog>
  );
}

/** Transient status message, announced politely to screen readers. */
export function Toast({ message, tone = "neutral" }: { message: string | null; tone?: "neutral" | "ok" | "bad" }) {
  if (!message) return null;
  const toneClass = tone === "ok" ? "text-ok" : tone === "bad" ? "text-bad" : "text-text";
  return (
    <div
      role="status"
      aria-live="polite"
      className={`glass-strong rise pointer-events-none fixed left-1/2 z-[60] -translate-x-1/2 !rounded-full px-5 py-3 text-[15px] font-medium ${toneClass}`}
      style={{ bottom: "calc(var(--dock-h) + 1.25rem + env(safe-area-inset-bottom, 0px))" }}
    >
      {message}
    </div>
  );
}
