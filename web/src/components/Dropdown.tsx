import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * Header dropdown: a button that opens a styled menu, closed by a pick, Escape or a click outside.
 * Used instead of <select> because a native popup can't be styled or capped in height.
 */
export function Dropdown({ label, ariaLabel, className, children }: {
  label: ReactNode; ariaLabel: string; className?: string; children: (close: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("pointerdown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);

  return (
    <div className={className ? `picker ${className}` : "picker"} ref={root}>
      <button className="picker-btn" aria-label={ariaLabel} aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {label} <span aria-hidden>▾</span>
      </button>
      {open && <div className="picker-menu" role="listbox" aria-label={ariaLabel}>{children(() => setOpen(false))}</div>}
    </div>
  );
}

/** One choice in a Dropdown menu. */
export function Choice({ on, onPick, children }: { on: boolean; onPick: () => void; children: ReactNode }) {
  return <button role="option" aria-selected={on} className={on ? "on" : ""} onClick={onPick}>{children}</button>;
}
