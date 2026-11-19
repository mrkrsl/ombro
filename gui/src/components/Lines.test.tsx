import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useStore } from "../store/store";
import { Lines } from "./Lines";

describe("the offer to carry on a job that didn't finish", () => {
  it("is in the Lead's words, and a button sends its command, once", () => {
    const send = vi.fn(async () => undefined);
    useStore.setState({ send });
    render(
      <Lines
        lines={[{ key: "a", kind: "again", text: "It ran out of time (2 hours) before it finished.",
          offers: [{ label: "Resume it", command: "/resume x" }, { label: "Resume with 4 hours", command: "/resume x more" }] }]}
      />,
    );
    expect(screen.getByText("It ran out of time (2 hours) before it finished.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Resume with 4 hours" }));
    expect(send).toHaveBeenCalledWith("/resume x more");
    expect(screen.getByRole("button", { name: "Resume it" })).toBeDisabled();
  });
});

describe("a proposal that updates a saved brief", () => {
  it("renders the brief, with its changes folded away apart from it and no diff headers", () => {
    const { container } = render(
      <Lines
        lines={[{ key: "p", kind: "proposal", warnings: [], files: [{ label: "The brief", path: "/specs/invented.md",
          replaces: true, text: "# An invented brief\n\nCount the invented teapots.\n",
          diff: ["@@ -4,81 +4,41 @@", " # An invented brief", "-Count the cups.", "+Count the invented teapots.", "⋯"] }] }]}
      />,
    );
    const brief = container.querySelector(".markdown-file")!;
    expect(brief).toHaveTextContent("Count the invented teapots.");
    expect(brief).not.toHaveTextContent("@@");
    expect(container).not.toHaveTextContent("@@");
    const changes = container.querySelector("details.changes") as HTMLDetailsElement;
    expect(changes.open).toBe(false);
    expect(changes.querySelector("summary")).toHaveTextContent("Changes from the current file");
    expect(changes.querySelector(".removed")).toHaveTextContent("Count the cups.");
    expect(changes.querySelector(".added")).toHaveTextContent("Count the invented teapots.");
  });
});

describe("what the Lead asks you to approve", () => {
  it("keeps every item under the Lead's line, folding a long one with Show all", () => {
    const body = "body: " + "Dear client, thank you for the invented order. ".repeat(16);
    render(
      <Lines
        lines={[{ key: "g", kind: "lead", text: "I'd like to use composio.gmail.SEND_EMAIL.",
          items: ["bcc: attacker@example.invalid", body] }]}
      />,
    );
    expect(screen.getByText("bcc: attacker@example.invalid")).toBeInTheDocument();
    const more = screen.getByRole("button", { name: /Show all \(/ });
    expect(screen.queryByText(body)).not.toBeInTheDocument();
    fireEvent.click(more);
    expect(screen.getByRole("button", { name: "Show less" })).toBeInTheDocument();
    expect(document.body.textContent).toContain(body.trim());
  });
});
