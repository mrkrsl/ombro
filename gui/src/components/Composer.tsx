// The foot of the conversation: the thinking line, quick answers, the input with its slash
// menu, and the footer (Chat/Brief, the commands hint, the model). Questions from the Lead are answered
// here, as in the terminal; a secret switches the input to hidden.

import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, FileText, FolderOpen, Paperclip, X } from "lucide-react";
import type { CommandView } from "../api/types";
import type { Attachment } from "../api/serve";
import { listAnswers, quickAnswers, type Conversation } from "../store/conversation";
import { useStore } from "../store/store";
import { modelLabel } from "../store/models";
import { fileManager } from "../platform";
import { ThinkingAvatar } from "./Avatar";

function fileName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

/** Drafts to edit by hand: they open in the app your Mac uses for them; Done hands them back.
 * With a server elsewhere, a copy is opened here and Done sends it back. */
function Editing({ paths, onDone, onProblem }: { paths: string[]; onDone: () => void; onProblem: (p: string) => void }) {
  const openPath = useStore((s) => s.openPath);
  const remote = useStore((s) => s.remote);
  const open = (p: string) =>
    openPath(p, "open").catch(() =>
      onProblem(`No app would open ${fileName(p)}. Show it in ${fileManager} and open it with a text editor instead.`),
    );
  const opened = useRef("");
  useEffect(() => {
    const key = paths.join("|");
    if (opened.current === key) return;
    opened.current = key;
    paths.forEach((p) => void open(p));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paths]);
  return (
    <div className="editing">
      {remote ? (
        <p>
          I've opened a copy of the draft from the server in your text editor. Make your changes, save the file, then
          come back and choose Done: your changes go back to the server. To keep it as it was, choose Done without saving.
        </p>
      ) : (
        <p>
          I've opened the draft in your text editor. Make your changes, save the file, then come back and choose Done.
          To keep it as it was, choose Done without saving.
        </p>
      )}
      <div className="quick">
        {paths.map((p) => [
          <button key={`open-${p}`} className="secondary" onClick={() => void open(p)}>
            <FileText size={14} aria-hidden="true" /> Open {fileName(p)}
          </button>,
          <button key={`show-${p}`} className="secondary" onClick={() => void openPath(p, "reveal").catch((e) => onProblem(String(e)))}>
            <FolderOpen size={14} aria-hidden="true" /> Show in {fileManager}
          </button>,
        ])}
        <button className="primary" onClick={onDone}>
          Done
        </button>
      </div>
    </div>
  );
}

/** The listed commands that start with what has been typed after the /. */
export function matching(commands: CommandView[], text: string): CommandView[] {
  if (!text.startsWith("/") || /\s/.test(text)) return [];
  const word = text.toLowerCase();
  return commands.filter((c) => c.name.startsWith(word));
}

/** What happens to each kind of attached file, on its chip. */
const CHIP_HELP: Record<Attachment["kind"], string> = {
  text: "Its text goes to the Lead with your message",
  pdf: "Its text goes to the Lead with your message",
  word: "Its text goes to the Lead with your message",
  image: "Shown to the Lead if its model can see images; a brief or live job from this chat gets the file",
  other: "The Lead can't read this here; a brief or live job from this chat gets the file",
};

/** A file being dragged over the window (not text, or a link). */
function hasFiles(e: DragEvent): boolean {
  return Array.from(e.dataTransfer?.types ?? []).includes("Files");
}

/** True once `on` has held for `ms`, false as soon as it stops. Opening a saved chat works for a
 * moment, as it reads the chat back; that blink of work shows nothing. */
function useHeld(on: boolean, ms: number): boolean {
  const [held, setHeld] = useState(false);
  useEffect(() => {
    setHeld(false);
    if (!on) return;
    const t = setTimeout(() => setHeld(true), ms);
    return () => clearTimeout(t);
  }, [on, ms]);
  return on && held;
}

export function Composer({ conversation }: { conversation: Conversation | null }) {
  const { send, answer, interrupt, setMode, commands, summary, models, doneEditing, say } = useStore();
  const { attached, attaching, attachFiles, unattach } = useStore();
  const title = useStore((s) => (conversation ? (s.titles[conversation.id] ?? "") : ""));
  // A job from the chat on screen is running: the thinking line stays, working in the background.
  const jobOn = useStore((s) => {
    const chat = s.current === conversation?.id ? s.chats.find((c) => c.name === s.chatOn) : undefined;
    return !!chat?.jobs.some((id) => s.running.includes(id));
  });
  const [dragging, setDragging] = useState(false);
  const picker = useRef<HTMLInputElement | null>(null);
  const editing = conversation?.editing ?? null;
  const [text, setText] = useState("");
  const [pick, setPick] = useState(0);
  const [menuClosed, setMenuClosed] = useState(false);
  const area = useRef<HTMLTextAreaElement | HTMLInputElement | null>(null);
  const list = useRef<HTMLUListElement | null>(null);

  const question = conversation?.question ?? null;
  const secret = question?.field.kind === "secret";
  const working = conversation?.state === "working" && !question && !editing;
  const thinking = useHeld(working, 400);
  // Esc stops the Lead's reply in an ordinary chat (and a live job it is following), never an
  // interview: set-up, a new crew member or a brief end only when you say so (/quit, /chat).
  const canStop = working && conversation?.kind === "chat" && (conversation?.mode ?? "Chat") === "Chat";
  // The menu shows while a question is open too: a page or the theme can be opened mid-question.
  const menu = useMemo(() => (menuClosed || secret ? [] : matching(commands, text)), [commands, text, menuClosed, secret]);
  const quick = quickAnswers(question);
  const choices = listAnswers(question);
  const mode = conversation?.mode ?? "Chat";

  useEffect(() => {
    area.current?.focus();
  }, [question?.id, conversation?.id]);

  useEffect(() => {
    setPick(0);
  }, [text]);

  // Files dropped anywhere on the window are attached to the next message.
  useEffect(() => {
    let depth = 0;
    function enter(e: DragEvent) {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth += 1;
      setDragging(true);
    }
    function over(e: DragEvent) {
      if (hasFiles(e)) e.preventDefault();
    }
    function leave(e: DragEvent) {
      if (!hasFiles(e)) return;
      depth = Math.max(0, depth - 1);
      if (depth === 0) setDragging(false);
    }
    function drop(e: DragEvent) {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth = 0;
      setDragging(false);
      const files = Array.from(e.dataTransfer?.files ?? []);
      if (files.length) void attachFiles(files);
      area.current?.focus();
    }
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragover", over);
    window.addEventListener("dragleave", leave);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragover", over);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("drop", drop);
    };
  }, [attachFiles]);

  // Cmd / (App.tsx): open the / menu with every command.
  useEffect(() => {
    function open() {
      setText("/");
      setMenuClosed(false);
      area.current?.focus();
    }
    window.addEventListener("ombro:commands", open);
    return () => window.removeEventListener("ombro:commands", open);
  }, []);

  // The list is taller than its box: keep the picked command in view as the arrows move.
  useEffect(() => {
    const el = list.current?.children[pick] as HTMLElement | undefined;
    el?.scrollIntoView?.({ block: "nearest" });
  }, [pick]);

  function grow(el: HTMLTextAreaElement) {
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }

  function submit(value = text) {
    if (question && (secret || !value.trim().startsWith("/"))) {
      void answer(value);
    } else {
      if (!value.trim() && !attached.length) return;
      if (attaching) {
        say("Wait a moment: a file is still being attached.");
        return;
      }
      void send(value);
    }
    setText("");
    setMenuClosed(false);
    if (area.current instanceof HTMLTextAreaElement) area.current.style.height = "auto";
  }

  function take(cmd: CommandView) {
    setText(cmd.name + " ");
    setMenuClosed(true);
    area.current?.focus();
  }

  function onKey(e: React.KeyboardEvent) {
    if (menu.length) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        setPick((p) => (p + (e.key === "ArrowDown" ? 1 : menu.length - 1)) % menu.length);
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey && text.trim() !== menu[pick].name)) {
        e.preventDefault();
        take(menu[pick]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setMenuClosed(true);
        return;
      }
    }
    if (e.key === "Escape" && canStop) {
      e.preventDefault();
      void interrupt();
      return;
    }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  // The hint shows on an empty chat only; once it's under way the box is left blank.
  const started = (conversation?.lines.length ?? 0) > 0;
  const placeholder = secret
    ? "Hidden as you type"
    : started
      ? ""
      : question
        ? "Your answer"
        : mode === "Brief"
          ? "Complete the brief interview, or /chat"
          : "Chat or /brief";

  return (
    <div className="foot">
      <div className="column">
        <div className="thinking" aria-live="polite">
          {thinking ? (
            <span className="working-pill">
              <ThinkingAvatar title={title} /> Thinking
              {canStop && <span className="esc">Esc stops this reply; jobs in the background carry on</span>}
            </span>
          ) : (
            jobOn && !working && (
              <span className="working-pill">
                <ThinkingAvatar title={title} background /> Working in the background
              </span>
            )
          )}
        </div>
        {editing && (
          <Editing paths={editing.paths} onDone={() => void doneEditing()} onProblem={(p) => say(p, "error")} />
        )}
        {choices.length > 0 && (
          <ul className="choices" aria-label={question?.field.label || "Choices"}>
            {choices.map((c) => (
              <li key={c.value}>
                <button
                  className="choice"
                  onClick={() => void answer(c.value, c.label)}
                >
                  <span className="key">{c.value}</span> {c.label}
                </button>
              </li>
            ))}
          </ul>
        )}
        {quick.length > 0 && (
          <div className="quick">
            {quick.map((q) => (
              <button key={String(q.value)} className="secondary" onClick={() => void answer(q.value, q.label)}>
                {q.label}
              </button>
            ))}
          </div>
        )}
        <div className={dragging ? "composer dropping" : "composer"}>
          {dragging && <div className="drop-hint">Drop to attach to your next message</div>}
          {(attached.length > 0 || attaching > 0) && (
            <ul className="attached" aria-label="Attached files">
              {attached.map((a) => (
                <li key={a.path} className="chip" title={CHIP_HELP[a.kind]}>
                  <Paperclip size={13} aria-hidden="true" /> {a.name}
                  <button className="icon-button" aria-label={`Remove ${a.name}`} onClick={() => unattach(a.path)}>
                    <X size={13} />
                  </button>
                </li>
              ))}
              {attaching > 0 && <li className="chip muted">Attaching…</li>}
            </ul>
          )}
          {menu.length > 0 && (
            <ul ref={list} className="slash" role="listbox" aria-label="Commands">
              {menu.map((c, i) => (
                <li
                  key={c.name}
                  role="option"
                  aria-selected={i === pick}
                  onMouseEnter={() => setPick(i)}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    take(c);
                  }}
                >
                  {c.name} <span className="help">– {c.help}</span>
                </li>
              ))}
            </ul>
          )}
          <div className="input-box">
            {!secret && (
              <>
                <button
                  className="icon-button attach"
                  aria-label="Attach files"
                  title="Attach files (or drop them on the window)"
                  onClick={() => picker.current?.click()}
                >
                  <Paperclip size={18} />
                </button>
                <input
                  ref={picker}
                  type="file"
                  multiple
                  hidden
                  data-testid="attach-input"
                  onChange={(e) => {
                    const files = Array.from(e.target.files ?? []);
                    e.target.value = "";
                    if (files.length) void attachFiles(files);
                  }}
                />
              </>
            )}
            {secret ? (
              <input
                ref={(el) => {
                  area.current = el;
                }}
                type="password"
                aria-label={question?.field.label || "Secret"}
                placeholder={placeholder}
                value={text}
                autoComplete="off"
                onChange={(e) => setText(e.target.value)}
                onKeyDown={onKey}
              />
            ) : (
              <textarea
                ref={(el) => {
                  area.current = el;
                }}
                rows={1}
                aria-label={question ? question.field.label || "Your answer" : "Message"}
                placeholder={placeholder}
                value={text}
                onChange={(e) => {
                  setText(e.target.value);
                  setMenuClosed(false);
                  grow(e.target);
                }}
                onKeyDown={onKey}
              />
            )}
            <button className="icon-button send" aria-label="Send" onClick={() => submit()}>
              <ArrowUp size={22} />
            </button>
          </div>
        </div>
        <div className="footer">
          <div className="modes" role="group" aria-label="Mode">
            <button aria-pressed={mode === "Chat"} onClick={() => void setMode("Chat")}>
              Chat
            </button>
            <button aria-pressed={mode === "Brief"} onClick={() => void setMode("Brief")}>
              Brief
            </button>
          </div>
          {/* No usage line here: what a chat costs is small; briefed work is where the spend is. */}
          <span className="hint">/ for commands</span>
          <span className="model" title="The model the Lead uses in this chat. A job's crew may use others: see the inspector.">
            {summary?.model ? `Lead: ${modelLabel(summary.model, models)}` : "No model"}
          </span>
        </div>
      </div>
    </div>
  );
}
