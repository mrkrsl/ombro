// A conversation (designs 03–05): the lines, kept scrolled to the newest.

import { useEffect, useRef } from "react";
import { Lines } from "../components/Lines";
import type { Conversation as C } from "../store/conversation";

/** Within this many pixels of the end counts as at the end. */
const NEAR_END = 48;

/** While you're at the end of the conversation, it stays there when its box changes height: a long
 * message growing the input box (or the box shrinking again) never hides the latest line.
 * Scrolled back to read something, you stay where you are. */
export function useStayAtEnd(page: React.RefObject<HTMLElement | null>) {
  useEffect(() => {
    const el = page.current;
    if (!el) return;
    let atEnd = true;
    const measure = () => {
      atEnd = el.scrollHeight - el.scrollTop - el.clientHeight <= NEAR_END;
    };
    el.addEventListener("scroll", measure, { passive: true });
    if (typeof ResizeObserver === "undefined") return () => el.removeEventListener("scroll", measure);
    const watch = new ResizeObserver(() => {
      if (atEnd) el.scrollTop = el.scrollHeight;
    });
    watch.observe(el);
    return () => {
      el.removeEventListener("scroll", measure);
      watch.disconnect();
    };
  }, [page]);
}

export function Conversation({ conversation }: { conversation: C }) {
  const page = useRef<HTMLDivElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const count = conversation.lines.length;
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [count, conversation.id]);
  useStayAtEnd(page);
  return (
    <div className="page" ref={page}>
      <div className="column">
        <Lines lines={conversation.lines} />
        <div ref={end} />
      </div>
    </div>
  );
}
