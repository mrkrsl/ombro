import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Line } from "../store/conversation";
import { apply, newConversation } from "../store/conversation";
import { scrollable } from "./Avatar";
import { Lines } from "./Lines";

// Each reply's avatar, as what it is told: which line, and whether it may move.
vi.mock("./Avatar", async (real) => ({
  ...(await real<typeof import("./Avatar")>()),
  ReplyAvatar: ({ id, moving }: { id: string; moving: boolean }) => (
    <span data-testid="avatar" data-id={id} data-moving={String(moving)} />
  ),
}));

const avatars = () => screen.queryAllByTestId("avatar").map((a) => [a.dataset.id, a.dataset.moving]);

describe("the Lead's avatars", () => {
  const turn: Line[] = [
    { key: "1", kind: "lead", text: "Hello." },
    { key: "2", kind: "you", text: "Look at competitors." },
    { key: "3", kind: "lead", text: "On it." },
    { key: "4", kind: "lead", text: "And one more thing." },
    { key: "5", kind: "note", text: "Saved.", items: [], level: "done" },
  ];

  it("shows one per turn of the Lead's, and only the newest may move", () => {
    render(<Lines lines={turn} chat="c" />);
    expect(avatars()).toEqual([["c:1", "false"], ["c:3", "true"]]);
  });

  it("keeps every one still while the Lead works", () => {
    render(<Lines lines={turn} chat="c" busy />);
    expect(avatars()).toEqual([["c:1", "false"], ["c:3", "false"]]);
  });

  it("never moves a reply from a reopened chat's past", () => {
    let c = newConversation("c");
    c = apply(c, { type: "event", session: "c", seq: 1, event: { type: "past", who: "you", text: "Hi" } } as never);
    c = apply(c, { type: "event", session: "c", seq: 2, event: { type: "past", who: "lead", text: "Hello." } } as never);
    render(<Lines lines={c.lines} chat="c" />);
    expect(avatars()).toEqual([["c:s2", "false"]]);
  });
});

describe("a chat title on the avatar", () => {
  it("keeps what the 3x5 font can show", () => {
    expect(scrollable("Café competitors — Q3 (draft) & notes")).toBe("CAFE COMPETITORS - Q3 DRAFT NOTES");
  });
});
