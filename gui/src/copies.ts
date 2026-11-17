// "Save to this computer": when serve runs on another machine, its files reach this one as copies kept in
// ~/Downloads/Ombro, under the path serve suggests (`<job id>/workspace/notes.md`). Files only come
// down, and only when asked. A draft edited by hand is the one thing sent back (on Done).
// In a browser there's no folder to keep copies in, so a file is downloaded the browser's way.

import { inTauri, type Client } from "./api/serve";

export type Copy = { local: string | null; saveAs: string };

/** Keep `data` at ~/Downloads/Ombro/<saveAs>; returns its full path. */
async function keep(saveAs: string, data: Uint8Array): Promise<string> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<string>("save_download", data, { headers: { "x-save-as": encodeURIComponent(saveAs) } });
}

function download(name: string, data: Uint8Array) {
  const url = URL.createObjectURL(new Blob([data as BlobPart]));
  const a = document.createElement("a");
  a.href = url;
  a.download = name.split("/").pop() || "file";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** One file from the server, kept on this machine. */
export async function saveCopy(api: Client, serverPath: string): Promise<Copy> {
  const { data, saveAs } = await api.bytes(serverPath);
  if (!inTauri) {
    download(saveAs, data);
    return { local: null, saveAs };
  }
  return { local: await keep(saveAs, data), saveAs };
}

type Listing = { folder: string; save_as: string; files: { path: string; save_as: string; size: number }[] };

/** Every file in a server folder (a job's workspace), kept with the folder's shape. Returns the
 * local folder, or null in a browser or when the folder is empty. */
export async function saveFolder(api: Client, serverFolder: string): Promise<{ local: string | null; count: number }> {
  const list = await api.get<Listing>(`/files/list?path=${encodeURIComponent(serverFolder)}`);
  let local: string | null = null;
  for (const f of list.files) {
    const copy = await saveCopy(api, f.path);
    if (copy.local && !local) local = copy.local.slice(0, copy.local.length - copy.saveAs.length) + list.save_as;
  }
  return { local, count: list.files.length };
}

/** The bytes of a copy kept earlier, to send back. App only. */
export async function readCopy(saveAs: string): Promise<Uint8Array> {
  const { invoke } = await import("@tauri-apps/api/core");
  const buf = await invoke<ArrayBuffer>("read_saved", { rel: saveAs });
  return new Uint8Array(buf);
}
