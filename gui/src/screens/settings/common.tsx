// What the Settings pages share: calling serve with the problem shown on the page (pages have no
// composer to show it under), deleting in two steps (what stops it, what to know, then yes), and
// a labelled field.

import { useCallback, useEffect, useState, type ReactNode } from "react";
import type { Client } from "../../api/serve";
import { useStore } from "../../store/store";

export type Check = { blocked: string | null; warnings: string[] };

/** Run calls to serve; a failure becomes `problem`, in serve's own words. */
export function useAction() {
  const api = useStore((s) => s.api);
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  const run = useCallback(
    async <T,>(fn: (api: Client) => Promise<T>): Promise<T | undefined> => {
      if (!api) return undefined;
      setProblem("");
      setBusy(true);
      try {
        return await fn(api);
      } catch (e) {
        setProblem(e instanceof Error ? e.message : String(e));
        return undefined;
      } finally {
        setBusy(false);
      }
    },
    [api],
  );
  return { run, problem, setProblem, busy, api };
}

/** Load a view when the page opens, and again on `reload()`. */
export function useView<T>(path: string): { data: T | null; reload: () => Promise<void>; set: (d: T) => void; problem: string } {
  const api = useStore((s) => s.api);
  const [data, setData] = useState<T | null>(null);
  const [problem, setProblem] = useState("");
  const reload = useCallback(async () => {
    if (!api) return;
    try {
      setData(await api.get<T>(path));
      setProblem("");
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
    }
  }, [api, path]);
  useEffect(() => {
    void reload();
  }, [reload]);
  return { data, reload, set: setData, problem };
}

/** Which row is being deleted, and what its check said. */
export function useRemoval() {
  const [target, setTarget] = useState<string | null>(null);
  const [check, setCheck] = useState<Check | null>(null);
  const [problem, setProblem] = useState("");
  async function ask(key: string, fn: () => Promise<Check>) {
    setTarget(key);
    setCheck(null);
    setProblem("");
    try {
      setCheck(await fn());
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
    }
  }
  function cancel() {
    setTarget(null);
    setCheck(null);
    setProblem("");
  }
  return { target, check, problem, ask, cancel, setProblem };
}

/** Under a row being deleted: why it can't be, or what to know and Yes/Cancel. */
export function RemovePanel({ removal, what, onConfirm }: {
  removal: ReturnType<typeof useRemoval>;
  what: string;
  onConfirm: () => Promise<boolean | void>;
}) {
  const { check, problem } = removal;
  if (problem) {
    return (
      <div className="remove-ask" role="alert">
        <p className="problem">{problem}</p>
        <button className="text-button strong" onClick={removal.cancel}>OK</button>
      </div>
    );
  }
  if (!check) return <div className="remove-ask muted">Checking…</div>;
  if (check.blocked) {
    return (
      <div className="remove-ask" role="alert">
        <p className="problem">{check.blocked}</p>
        <button className="text-button strong" onClick={removal.cancel}>OK</button>
      </div>
    );
  }
  return (
    <div className="remove-ask" role="group" aria-label={`Delete ${what}?`}>
      <p>
        <b>Delete {what}?</b> It's moved aside into Ombro's removed folder, not erased.
      </p>
      {check.warnings.map((w) => (
        <p key={w} className="muted">
          {w}
        </p>
      ))}
      <div className="remove-buttons">
        <button
          className="primary small"
          onClick={async () => {
            const ok = await onConfirm();
            if (ok !== false) removal.cancel();
          }}
        >
          Delete
        </button>
        <button className="text-button strong" onClick={removal.cancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

export function Problem({ text }: { text: string }) {
  return text ? (
    <p className="problem" role="alert">
      {text}
    </p>
  ) : null;
}

/** "invented-fact-check" → "Invented fact check". */
export function tidy(name: string): string {
  const s = name.replace(/[-_.]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** A path under the home folder, as people write it: ~/Projects/work. */
export function shortPath(path: string): string {
  return path.replace(/^\/(Users|home)\/[^/]+/, "~");
}
