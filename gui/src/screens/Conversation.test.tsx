// T23: the conversation keeps its latest line in view as the input box grows or shrinks.
import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { newConversation } from "../store/conversation";
import { Conversation } from "./Conversation";

let resized: (() => void) | null = null;

class FakeResizeObserver {
  constructor(cb: () => void) {
    resized = cb;
  }
  observe() {}
  disconnect() {}
}

function sized(el: HTMLElement, box: { scrollHeight: number; clientHeight: number }) {
  Object.defineProperty(el, "scrollHeight", { configurable: true, get: () => box.scrollHeight });
  Object.defineProperty(el, "clientHeight", { configurable: true, get: () => box.clientHeight });
}

afterEach(() => {
  vi.unstubAllGlobals();
  resized = null;
});

describe("the conversation", () => {
  it("stays at its end when the input box grows, unless you scrolled back", () => {
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
    const { container } = render(<Conversation conversation={newConversation("s")} />);
    const page = container.querySelector(".page") as HTMLElement;
    const box = { scrollHeight: 1000, clientHeight: 600 };
    sized(page, box);
    page.scrollTop = 400; // at the end
    page.dispatchEvent(new Event("scroll"));

    box.clientHeight = 480; // a long message: the input grew, the conversation's box shrank
    resized!();
    expect(page.scrollTop).toBe(1000); // the browser clamps this to the new end: 520

    page.scrollTop = 100; // scrolled back to read
    page.dispatchEvent(new Event("scroll"));
    box.clientHeight = 600; // the input shrank after sending
    resized!();
    expect(page.scrollTop).toBe(100);
  });
});
