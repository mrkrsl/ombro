// Where Ombro runs: this computer, or a server (the shell's connection.rs). These wrap its
// commands. In a browser (`ombro serve --dev`) there is no choice: serve is whatever printed the URL.

import { inTauri } from "./api/serve";

export type Where = {
  mode: "unset" | "local" | "ssh" | "direct";
  host: string | null;
  port: number | null;
  address: string | null;
  local_home: boolean; // this computer has an Ombro home of its own
  started: boolean;
  /** While on this computer: the server used before, whose key is still kept here. */
  previous?: Server | null;
};

/** A server, as connection.rs describes one. */
export type Server = { mode: "ssh" | "direct"; host: string | null; port: number | null; address: string | null };

/** The protocol this app speaks (serve's PROTOCOL, in /hello). */
export const PROTOCOL = 1;

async function call<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<T>(cmd, args);
}

export async function whereNow(): Promise<Where | null> {
  return inTauri ? call<Where>("connection_state") : null;
}

/** Where it runs, in a few words: "this computer", "mark@box (over SSH)". */
export function describe(w: Where | null): string {
  if (!w || w.mode === "local" || w.mode === "unset") return "this computer";
  if (w.mode === "ssh") return `${w.host} (over SSH)`;
  return w.address ?? "a server";
}

export const useThisComputer = () => call<void>("use_this_computer");

/** What a key kept from pairing before gets from a server: it still knows this computer (and is
 *  now the connection, ready to start), it has forgotten it, or no key is kept for it here. */
export type Kept = "known" | "forgotten" | "none";

/** Use a server paired with before, with the key kept for it: no code. */
export function useKept(server: { host?: string; port?: number; address?: string }): Promise<Kept> {
  return call<Kept>("use_kept", {
    host: server.host ?? null,
    port: server.port ?? null,
    address: server.address ?? null,
  });
}

/** Why a kept key no longer works, and what to do. */
export function forgotten(name: string): string {
  return `${name} no longer knows this computer, so it needs pairing again. Type the pairing code the server shows (run ombro pair there for a new one).`;
}
export const startConnection = () => call<void>("start_connection");

/** Pair over SSH: the app signs in with your SSH key and asks the server for a key of its own.
 *  With a code (Ombro in Docker on the server, which the server's own `ombro pair` can't reach),
 *  it pairs with that code instead, through a short-lived SSH tunnel. */
export async function pairOverSsh(host: string, port?: number, code?: string): Promise<void> {
  if (!code) {
    await call<void>("pair_over_ssh", { host, port: port ?? null });
    return;
  }
  const local = await call<number>("reach_over_ssh", { host, port: port ?? null });
  let token: string;
  try {
    token = await pairWithCode(local, code, `${host} (over SSH)`);
  } catch (e) {
    await call<void>("end_ssh_pairing").catch(() => undefined);
    throw e;
  }
  await call<void>("keep_ssh", { host, port: port ?? null, token });
}

/** Pair by address with the code the server showed. Returns nothing, or throws with why not. */
export async function pairByAddress(address: string, code: string): Promise<void> {
  const [port, kept] = await call<[number, string]>("reach_address", { address });
  const token = await pairWithCode(port, code, kept);
  await call<void>("keep_address", { address: kept, token });
}

/** Post a pairing code to a server reached at 127.0.0.1:<port>; its token, or why not. */
async function pairWithCode(port: number, code: string, kept: string): Promise<string> {
  const name = await call<string>("device_name");
  let r: Response;
  try {
    r = await fetch(`http://127.0.0.1:${port}/pair`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, name }),
    });
  } catch {
    throw new Error(`Nothing answered at ${kept}. Check that the Ombro server is running there and the address is right.`);
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok || !data.token) throw new Error(data.error || `The server said no (${r.status}).`);
  if (data.protocol !== PROTOCOL) throw new Error(tooOld(data.version));
  return data.token;
}

export function tooOld(version: string | undefined): string {
  return `The server runs Ombro ${version ?? "(an older version)"}, which this app can't work with. Update Ombro on the server and this app to the same version.`;
}
