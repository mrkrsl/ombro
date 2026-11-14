// Finding and talking to `ombro serve`.
//
// In the app, the shell starts serve and hands over its handshake. In an
// ordinary browser (`ombro serve --dev` with Vite), the port and token come
// from the URL that serve printed.

import { invoke } from "@tauri-apps/api/core";

export type Handshake = { port: number; token: string };

/** A core event, as `ui.py`'s `to_dict()` gives it. */
export type CoreEvent = { type: string; text?: string; [key: string]: unknown };

export type EventMessage = { type: "event"; session: string | null; seq: number; event: CoreEvent };

const inTauri = "__TAURI_INTERNALS__" in window;

export async function handshake(): Promise<Handshake> {
  if (inTauri) return invoke<Handshake>("handshake");
  const q = new URLSearchParams(location.search);
  const port = Number(q.get("port"));
  const token = q.get("token");
  if (!port || !token) throw new Error("Open the URL that `ombro serve --dev` printed.");
  return { port, token };
}

/** Opens the event socket. The token goes as a subprotocol: browsers can't set headers on a socket. */
export function events(hs: Handshake, onMessage: (m: EventMessage) => void, onClose: (why: string) => void): WebSocket {
  const ws = new WebSocket(`ws://127.0.0.1:${hs.port}/events`, ["ombro", hs.token]);
  ws.onmessage = (e) => onMessage(JSON.parse(e.data));
  ws.onclose = (e) => onClose(e.code === 1000 ? "closed" : `closed (${e.code})`);
  return ws;
}
