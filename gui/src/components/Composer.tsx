// The foot of the conversation: the thinking line, quick answers, the input with its slash
// menu, and the footer (Chat/Brief, the model, usage). Questions from the Lead are answered
// here, as in the terminal; a secret switches the input to hidden.

import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, CircleDashed } from "lucide-react";
import type { CommandView } from "../api/types";
import { quickAnswers, usageText, type Conversation } from "../store/conversation";
import { useStore } from "../store/store";

/** The listed commands that start with what has been typed after the /. */
export function matching(commands: CommandView[], text: string): CommandView[] {
  if (!text.startsWith("/") || /\s/.test(text)) return [];
  const word = text.toLowerCase();
  return commands.filter((c) => c.name.startsWith(word));
}

export function Composer({ conversation }: { conversation: Conversation | null }) {
  const { send, answer, interrupt, setMode, commands, summary, notice } = useStore();
  const [text, setText] = useState("");
  const [pick, setPick] = useState(0);
  const [menuClosed, setMenuClosed] = useState(false);
  const area = useRef<HTMLTextAreaElement | HTMLInputElement | null>(null);

  const question = conversation?.question ?? null;
  const secret = question?.field.kind === "secret";
  const working = conversation?.state === "working" && !question;
  const menu = useMemo(
    () => (question || menuClosed ? [] : matching(commands, text)),
    [commands, text, question, menuClosed],
  );
  const quick = quickAnswers(question);
  const mode = conversation?.mode ?? "Chat";

  useEffect(() => {
    area.current?.focus();
  }, [question?.id, conversation?.id]);

  useEffect(() => {
    setPick(0);
  }, [text]);

  function grow(el: HTMLTextAreaElement) {
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }

  function submit(value = text) {
    if (question) {
      void answer(value);
    } else {
      if (!value.trim()) return;
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
    if (e.key === "Escape" && (working || question)) {
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
          {working && (
            <>
              <CircleDashed size={18} aria-hidden="true" /> Thinking
            </>
          )}
        </div>
        {quick.length > 0 && (
          <div className="quick">
            {quick.map((q) => (
              <button key={String(q.value)} className="secondary" onClick={() => void answer(q.value, q.label)}>
                {q.label}
              </button>
            ))}
          </div>
        )}
        <div className="composer">
          {menu.length > 0 && (
            <ul className="slash" role="listbox" aria-label="Commands">
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
          <span className="model">{summary?.model ?? "No model"}</span>
          <span className="hint">/ for commands</span>
          <span className="usage">{usageText(conversation?.usage ?? null) || "Usage: 0%"}</span>
        </div>
        {notice && <div className="notice">{notice}</div>}
      </div>
    </div>
  );
}
