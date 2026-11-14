// Stage 1: start serve (the shell does that), connect, and show what it sends.

import { useEffect, useState } from "react";
import { events, handshake, type EventMessage } from "./api/serve";

type State = { status: string; messages: EventMessage[] };

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
          (m) => setState((s) => ({ status: "Connected", messages: [...s.messages, m] })),
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
        <section key={m.seq} style={{ borderTop: "1px solid var(--rule)", padding: "16px 0" }}>
          <p style={{ fontSize: 20, margin: "0 0 12px" }} data-testid="event-text">{m.event.text}</p>
          <pre style={{ color: "var(--muted)", fontSize: 12, margin: 0, whiteSpace: "pre-wrap" }}>
            {JSON.stringify(m, null, 2)}
          </pre>
        </section>
      ))}
    </main>
  );
}
