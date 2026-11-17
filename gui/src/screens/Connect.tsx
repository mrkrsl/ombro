// Where Ombro runs: the first question on a fresh install, the way back when a server can't be
// reached, and the form behind Settings › General › Where Ombro runs. On this computer, Ombro is as
// it always was. On a server, the app connects over SSH (no code: your SSH key signs in) or by
// address (Tailscale, or Docker on this computer), with the pairing code the server showed.

import { useState } from "react";
import { Laptop, Server } from "lucide-react";
import { useStore } from "../store/store";
import { pairByAddress, pairOverSsh, useThisComputer } from "../where";

type How = "ssh" | "address";

export function ServerForm({ onConnected, onCancel }: { onConnected: () => void; onCancel?: () => void }) {
  // Start from the server used last, if there was one: reconnecting is usually to the same one.
  const last = useStore((s) => s.where);
  const [how, setHowNow] = useState<How>(last?.mode === "direct" ? "address" : "ssh");
  const [host, setHost] = useState(last?.mode === "ssh" ? last.host ?? "" : "");
  const [port, setPort] = useState(last?.mode === "ssh" && last.port && last.port !== 8765 ? String(last.port) : "");
  const [address, setAddress] = useState(last?.mode === "direct" ? last.address ?? "" : "");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const setHow = (h: How) => {
    setHowNow(h);
    setProblem("");
  };

  async function connect() {
    setBusy(true);
    setProblem("");
    try {
      if (how === "ssh") await pairOverSsh(host, port.trim() ? Number(port) : undefined);
      else await pairByAddress(address, code);
      onConnected();
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const ready = how === "ssh" ? host.trim() !== "" && (!port.trim() || Number(port) > 0) : address.trim() !== "" && code.trim() !== "";
  return (
    <div className="inline-form connect-form">
      <h3>Connect to a server</h3>
      <div className="modes" role="group" aria-label="How to connect">
        <button aria-pressed={how === "ssh"} onClick={() => setHow("ssh")}>
          Over SSH
        </button>
        <button aria-pressed={how === "address"} onClick={() => setHow("address")}>
          By address
        </button>
      </div>
      {how === "ssh" ? (
        <>
          <label className="field">
            <span className="field-label">Server</span>
            <input
              className="text-input"
              aria-label="Server"
              placeholder="you@server.example.com"
              value={host}
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              onChange={(e) => setHost(e.target.value)}
            />
            <span className="field-hint">
              Ombro signs in with your SSH key, as ssh does in a terminal, and pairs by itself. Ombro must be installed and
              running there (ombro serve install).
            </span>
          </label>
          <details className="more">
            <summary>Ombro's port on the server</summary>
            <input
              className="text-input short"
              aria-label="Port"
              inputMode="numeric"
              placeholder="8765"
              value={port}
              onChange={(e) => setPort(e.target.value.replace(/\D/g, ""))}
            />
          </details>
        </>
      ) : (
        <>
          <label className="field">
            <span className="field-label">Address</span>
            <input
              className="text-input"
              aria-label="Address"
              placeholder="100.101.102.103:8765"
              value={address}
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              onChange={(e) => setAddress(e.target.value)}
            />
            <span className="field-hint">
              A Tailscale address or name, or 127.0.0.1:8765 for Ombro in Docker on this computer.
            </span>
          </label>
          <label className="field">
            <span className="field-label">Pairing code</span>
            <input
              className="text-input short"
              aria-label="Pairing code"
              placeholder="ABC-DEF-GHJ"
              value={code}
              autoCapitalize="characters"
              autoCorrect="off"
              spellCheck={false}
              onChange={(e) => setCode(e.target.value)}
            />
            <span className="field-hint">
              The server shows it when it starts (for Docker, in the container's logs). For a new one, run ombro pair
              there.
            </span>
          </label>
        </>
      )}
      <div className="form-actions">
        <button className="primary" disabled={busy || !ready} onClick={() => void connect()}>
          {busy ? "Connecting…" : "Connect"}
        </button>
        {onCancel && (
          <button className="secondary" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
        )}
      </div>
      {problem && (
        <p className="problem" role="alert">
          {problem}
        </p>
      )}
    </div>
  );
}

/** The whole window, before Ombro has started: a fresh install, or a server that can't be reached. */
export function Connect({ problem, onReady }: { problem?: string; onReady: () => void }) {
  const [server, setServer] = useState(Boolean(problem));
  const [busy, setBusy] = useState(false);
  return (
    <div className="connect">
      <div className="connect-inner">
        <h1 className="page-title">{problem ? "Ombro can't reach its server" : "Where should Ombro run?"}</h1>
        {problem ? (
          <p className="problem">{problem}</p>
        ) : (
          <p className="lede">You can change this later in Settings.</p>
        )}
        {!server && (
          <div className="where-choices">
            <button
              className="where-choice"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                await useThisComputer();
                onReady();
              }}
            >
              <Laptop size={22} aria-hidden="true" />
              <span>
                <b>On this computer</b>
                <span>Ombro, its settings and its files stay here.</span>
              </span>
            </button>
            <button className="where-choice" onClick={() => setServer(true)}>
              <Server size={22} aria-hidden="true" />
              <span>
                <b>On a server</b>
                <span>Ombro runs on a server you've installed it on, and this app connects to it. Jobs can't reach this computer's files.</span>
              </span>
            </button>
          </div>
        )}
        {server && (
          <>
            <ServerForm onConnected={onReady} onCancel={problem ? undefined : () => setServer(false)} />
            {problem && (
              <p>
                <button
                  className="link-button"
                  onClick={async () => {
                    await useThisComputer();
                    onReady();
                  }}
                >
                  Use Ombro on this computer instead
                </button>
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
