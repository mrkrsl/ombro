// Settings › Folders: the folders shared with the Lead and every crew, how much they may
// do there, and any problem. Add picks a folder with the system's own picker; Remove stops sharing it
// (the folder itself is never touched).

import { useState } from "react";
import { inTauri } from "../../api/serve";
import type { FolderList } from "../../api/types";
import { useStore } from "../../store/store";
import { Field, Item, Problem, shortPath, useAction, useView } from "./common";

export const ACCESS: Record<string, string> = { read: "Can read", write: "Can read and write" };

async function pickFolder(): Promise<string | null> {
  const { open } = await import("@tauri-apps/plugin-dialog");
  const picked = await open({ directory: true, multiple: false, title: "Share a folder with Ombro" });
  return typeof picked === "string" ? picked : null;
}

export function Folders() {
  const folders = useView<FolderList>("/folders");
  const { run, problem, busy } = useAction();
  const [path, setPath] = useState<string | null>(null);
  const [access, setAccess] = useState("read");
  const [asking, setAsking] = useState<string | null>(null);
  const list = folders.data?.folders ?? [];
  // With a server elsewhere, the folder is one on the server, so the Mac's picker can't choose it.
  const remote = useStore((s) => s.remote);
  const picker = inTauri && !remote;

  async function start() {
    if (!picker) {
      setPath(""); // in a browser, or for a server elsewhere, there's no picker: type the path
      return;
    }
    const picked = await pickFolder();
    if (picked) setPath(picked);
  }

  return (
    <>
      <h1 className="page-title">Connected folders</h1>
      <p className="lede">
        Ombro's crews can only reach the folders you share here. Each one is open to the Lead and to every crew.
        Ombro's own files are never shared.
        {remote && " Ombro runs on a server, so these are folders on the server, not on this computer."}
      </p>
      <Problem text={folders.problem} />
      <div className="rows">
        {list.length === 0 && folders.data && <p className="muted">No folders are shared yet.</p>}
        {list.map((f) => (
          <div className="row-block" key={f.name}>
            <Item
              name={shortPath(f.path)}
              title={f.path}
              details={[
                ACCESS[f.access] ?? f.access,
                f.problem ? <span className="problem-inline">{f.problem}</span> : null,
              ]}
              actions={
                asking === f.name ? (
                  <span className="confirm" role="group" aria-label={`Stop sharing ${shortPath(f.path)}?`}>
                    <span>Stop sharing it? The folder stays as it is.</span>
                    <button
                      className="text-button strong"
                      onClick={async () => {
                        const next = await run((api) => api.del<FolderList>(`/folders/${encodeURIComponent(f.name)}`));
                        if (next) folders.set(next);
                        setAsking(null);
                      }}
                    >
                      Yes
                    </button>
                    <button className="text-button" onClick={() => setAsking(null)}>
                      No
                    </button>
                  </span>
                ) : (
                  <button className="text-button strong" onClick={() => setAsking(f.name)}>
                    Remove
                  </button>
                )
              }
            />
          </div>
        ))}
      </div>
      {path === null ? (
        <div className="form-actions">
          <button className="primary" onClick={() => void start()}>
            Add a folder
          </button>
        </div>
      ) : (
        <div className="inline-form">
          <h3>Share a folder</h3>
          {picker ? (
            <p className="chosen-path">
              {shortPath(path)}{" "}
              <button className="text-button" onClick={() => void start()}>
                Choose another
              </button>
            </p>
          ) : (
            <Field
              label="Folder"
              hint={remote ? "The folder's full path on the server, such as /home/you/work." : "The full path, such as /Users/you/Projects/work."}
            >
              <input className="text-input" value={path} onChange={(e) => setPath(e.target.value)} />
            </Field>
          )}
          <Field
            label="What may crews do there?"
            hint={access === "write" ? "Ombro keeps a copy of anything it changes or deletes, so nothing is lost." : undefined}
          >
            <span className="form-row tight">
              <select aria-label="Access" value={access} onChange={(e) => setAccess(e.target.value)}>
                <option value="read">Read only</option>
                <option value="write">Read and write</option>
              </select>
            </span>
          </Field>
          <div className="form-actions">
            <button
              className="primary"
              disabled={busy || !path.trim()}
              onClick={async () => {
                const next = await run((api) => api.post<FolderList>("/folders", { path: path.trim(), access }));
                if (next) {
                  folders.set(next);
                  setPath(null);
                  setAccess("read");
                }
              }}
            >
              Share it
            </button>
            <button className="secondary" onClick={() => setPath(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      <Problem text={problem} />
    </>
  );
}
