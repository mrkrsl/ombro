// The Lead's words and reports are Markdown. Links open outside the window.

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useStore } from "../store/store";

export function Markdown({ text, className = "" }: { text: string; className?: string }) {
  return (
    <div className={`markdown ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => (
            <a
              href={href}
              onClick={(e) => {
                e.preventDefault();
                if (href) void useStore.getState().openHref(href);
              }}
            >
              {children}
            </a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

/** A Markdown file's settings header (between `---` lines at the top) and its body. */
export function splitFrontMatter(text: string): { settings: string; body: string } {
  const m = /^---\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/.exec(text);
  return m ? { settings: m[1].trim(), body: text.slice(m[0].length).trim() } : { settings: "", body: text };
}

/** A whole Markdown file, as a brief or a crew member's file: its settings as a short list
 * of names and values (or as written, when they are more than that), then the body formatted. */
export function MarkdownFile({ text }: { text: string }) {
  const { settings, body } = splitFrontMatter(text);
  const lines = settings ? settings.split(/\r?\n/) : [];
  const simple = lines.every((l) => /^[\w.-]+:\s*\S/.test(l));
  return (
    <div className="markdown-file">
      {settings &&
        (simple ? (
          <ul className="settings">
            {lines.map((l) => {
              const at = l.indexOf(":");
              return (
                <li key={l}>
                  <b>{l.slice(0, at)}:</b> {l.slice(at + 1).trim()}
                </li>
              );
            })}
          </ul>
        ) : (
          <pre className="settings">{settings}</pre>
        ))}
      {body && <Markdown text={body} />}
    </div>
  );
}
