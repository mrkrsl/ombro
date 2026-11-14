// A conversation (designs 03–05): the lines, kept scrolled to the newest.

import { useEffect, useRef } from "react";
import { Lines } from "../components/Lines";
import type { Conversation as C } from "../store/conversation";

export function Conversation({ conversation }: { conversation: C }) {
  const end = useRef<HTMLDivElement>(null);
  const count = conversation.lines.length;
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [count, conversation.id]);
  return (
    <div className="page">
      <div className="column">
        <Lines lines={conversation.lines} />
        <div ref={end} />
      </div>
    </div>
  );
}
