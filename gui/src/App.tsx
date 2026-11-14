// Stage 1: start serve (the shell does that), connect, and show what it sends.

import { useEffect, useState } from "react";
import { events, handshake, type CoreEvent, type ServerMessage } from "./api/serve";

type Shown = { key: number; event: CoreEvent; raw: ServerMessage };
type State = { status: string; messages: Shown[] };
let shownCount = 0;

export default function App() {
  const [state, setState] = useState<State>({ status: "Starting Ombro…", messages: [] });

  useEffect(() => {
    let ws: WebSocket | undefined;
    let gone = false;
    handshake()
      .then((hs) => {
        if (gone) return;
        setState((s) => ({ ...s, status: `Connecting to 127.0.0.1:${hs.port}…` }));
        ws = events(
          hs,
          (m) => {
            // Stage 1 shows the core's events only; the screens come in stage 4.
            if (m.type !== "event") return;
            const shown = { key: ++shownCount, event: m.event, raw: m };
            setState((s) => ({ status: "Connected", messages: [...s.messages, shown] }));
          },
          (why) => setState((s) => ({ ...s, status: `Connection ${why}` })),
        );
      })
      .catch((e) => setState((s) => ({ ...s, status: `Could not start: ${e}` })));
    return () => {
      gone = true;
      ws?.close(1000);
    };
  }, []);

  return (
    <main style={{ padding: "48px 56px", maxWidth: 720 }}>
      <h1 style={{ color: "var(--strong)", fontWeight: 600, margin: "0 0 4px" }}>ombro</h1>
      <p style={{ color: "var(--muted)", margin: "0 0 32px" }} data-testid="status">{state.status}</p>
      {state.messages.map((m) => (
        <section key={m.key} style={{ borderTop: "1px solid var(--rule)", padding: "16px 0" }}>
          <p style={{ fontSize: 20, margin: "0 0 12px" }} data-testid="event-text">
            {"text" in m.event ? m.event.text : m.event.type}
          </p>
          <pre style={{ color: "var(--muted)", fontSize: 12, margin: 0, whiteSpace: "pre-wrap" }}>
            {JSON.stringify(m.raw, null, 2)}
          </pre>
        </section>
      ))}
    </main>
  );
}
