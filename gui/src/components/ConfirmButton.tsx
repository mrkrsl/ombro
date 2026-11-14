// A button for something that can't be taken back (stop a job, delete a schedule): the first
// click asks, inline, and only "Yes" does it.

import { useState } from "react";

export function ConfirmButton({ label, ask, onConfirm, className = "text-button" }: {
  label: string;
  ask: string;
  onConfirm: () => void | Promise<void>;
  className?: string;
}) {
  const [asking, setAsking] = useState(false);
  if (!asking) {
    return (
      <button className={className} onClick={() => setAsking(true)}>
        {label}
      </button>
    );
  }
  return (
    <span className="confirm" role="group" aria-label={ask}>
      <span>{ask}</span>
      <button
        className="text-button strong"
        onClick={async () => {
          setAsking(false);
          await onConfirm();
        }}
      >
        Yes
      </button>
      <button className="text-button" onClick={() => setAsking(false)}>
        No
      </button>
    </span>
  );
}
