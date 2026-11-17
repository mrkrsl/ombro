// Toasts: short notices in the window's top-right corner, newest at the bottom. The store decides
// what each one is (timed, dismissable, both, or persistent); this only shows them.

import { X } from "lucide-react";
import { useStore } from "../store/store";

export function Toasts() {
  const toasts = useStore((s) => s.toasts);
  const dismiss = useStore((s) => s.dismissToast);
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.tone}`} role={t.tone === "error" ? "alert" : "status"}>
          <span className="toast-text">{t.text}</span>
          {t.dismissable && (
            <button className="toast-close" aria-label="Dismiss" onClick={() => dismiss(t.id)}>
              <X size={15} />
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

/** Over the whole window while the app restarts to switch where Ombro runs. */
export function Restarting() {
  return (
    <div className="restarting" role="status">
      Restarting…
    </div>
  );
}
