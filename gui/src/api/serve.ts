// Finding and talking to `ombro serve`.
//
// In the app, the shell starts serve and hands over its handshake. In an
// ordinary browser (`ombro serve --dev` with Vite), the port and token come
// from the URL that serve printed.

import { invoke } from "@tauri-apps/api/core";
import type { CoreEvent, ServerMessage } from "./types";

/** `remote`: serve is on another machine (or in a container), so its files aren't on this one. */
export type Handshake = { port: number; token: string; remote?: boolean };
export type { CoreEvent, ServerMessage };

export const inTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export async function handshake(): Promise<Handshake> {
  if (inTauri) return invoke<Handshake>("handshake");
  const q = new URLSearchParams(location.search);
  const port = Number(q.get("port"));
  const token = q.get("token");
  if (!port || !token) throw new Error("Open the URL that `ombro serve --dev` printed.");
  // ?remote=1 tries the app as it is with a server elsewhere: files come as downloads.
  return { port, token, remote: q.get("remote") === "1" };
}

/** Opens the event socket. The token goes as a subprotocol: browsers can't set headers on a socket. */
export function events(hs: Handshake, onMessage: (m: ServerMessage) => void, onClose: (why: string) => void): WebSocket {
  const ws = new WebSocket(`ws://127.0.0.1:${hs.port}/events`, ["ombro", hs.token]);
  ws.onmessage = (e) => onMessage(JSON.parse(e.data));
  ws.onclose = (e) => onClose(e.code === 1000 ? "closed" : `closed (${e.code})`);
  return ws;
}

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

/** HTTP to serve, with the token. Replies are JSON; an error reply becomes an ApiError with serve's words. */
export function client(hs: Handshake) {
  const base = `http://127.0.0.1:${hs.port}`;
  async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const r = await fetch(base + path, {
      method,
      headers: { Authorization: `Bearer ${hs.token}`, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new ApiError(r.status, (data && data.error) || `${r.status}`);
    return data as T;
  }
  /** A file's bytes from GET /files, and where serve suggests keeping the copy. */
  async function bytes(path: string): Promise<{ data: Uint8Array; saveAs: string }> {
    const r = await fetch(`${base}/files?path=${encodeURIComponent(path)}`, { headers: { Authorization: `Bearer ${hs.token}` } });
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      throw new ApiError(r.status, (data && data.error) || `${r.status}`);
    }
    const saveAs = decodeURIComponent(r.headers.get("x-ombro-save-as") || path.split("/").pop() || "file");
    return { data: new Uint8Array(await r.arrayBuffer()), saveAs };
  }
  /** PUT /files: a draft edited by hand goes back to the server. */
  async function putBytes(path: string, data: Uint8Array): Promise<void> {
    const r = await fetch(`${base}/files?path=${encodeURIComponent(path)}`, {
      method: "PUT",
      headers: { Authorization: `Bearer ${hs.token}`, "Content-Type": "application/octet-stream" },
      body: data as BodyInit,
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new ApiError(r.status, (body && body.error) || `${r.status}`);
    }
  }
  /** POST /attachments: a file attached in the app, kept on the machine Ombro runs on. */
  async function attach(name: string, data: Blob): Promise<Attachment> {
    const r = await fetch(`${base}/attachments?name=${encodeURIComponent(name)}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${hs.token}`, "Content-Type": "application/octet-stream" },
      body: data,
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new ApiError(r.status, (body && body.error) || `${r.status}`);
    return body as Attachment;
  }
  return {
    bytes,
    putBytes,
    attach,
    get: <T>(path: string) => call<T>("GET", path),
    post: <T>(path: string, body: unknown = {}) => call<T>("POST", path, body),
    put: <T>(path: string, body: unknown) => call<T>("PUT", path, body),
    del: <T>(path: string) => call<T>("DELETE", path),
  };
}

export type Client = ReturnType<typeof client>;

/** A file attached to a message: where Ombro keeps it, and what it is. */
export type Attachment = { path: string; name: string; size: number; kind: "text" | "pdf" | "word" | "image" | "other" };
