// If a screen fails to draw, say so in the window rather than leaving it blank.

import { Component, type ReactNode } from "react";

export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="startup">
        <div>
          <p>Something in this window went wrong.</p>
          <pre style={{ whiteSpace: "pre-wrap", maxWidth: 640, textAlign: "left" }}>
            {String(this.state.error.stack || this.state.error)}
          </pre>
        </div>
      </div>
    );
  }
}
