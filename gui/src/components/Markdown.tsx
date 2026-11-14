// The Lead's words and reports are Markdown. Links open outside the window.

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { openLink } from "../platform";

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
                if (href) void openLink(href);
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
