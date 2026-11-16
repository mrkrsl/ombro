import { describe, expect, it } from "vitest";
import { shortcutFor, shortcutText } from "./shortcuts";

const press = (key: string, mods: Partial<{ metaKey: boolean; ctrlKey: boolean; shiftKey: boolean; altKey: boolean }> = {}) =>
  ({ key, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, ...mods });

describe("shortcuts", () => {
  it("uses Cmd on macOS and Ctrl elsewhere", () => {
    expect(shortcutFor(press("n", { metaKey: true }), true)).toBe("new");
    expect(shortcutFor(press("n", { ctrlKey: true }), true)).toBeNull();
    expect(shortcutFor(press("n", { ctrlKey: true }), false)).toBe("new");
    expect(shortcutFor(press("b", { metaKey: true }), true)).toBe("sidebar");
    expect(shortcutFor(press(",", { metaKey: true }), true)).toBe("settings");
    expect(shortcutFor(press("j", { metaKey: true }), true)).toBe("jobs");
    expect(shortcutFor(press("d", { metaKey: true }), true)).toBe("scheduled");
  });

  it("leaves Cmd A as Select All: Approvals is Cmd Shift A", () => {
    expect(shortcutFor(press("a", { metaKey: true }), true)).toBeNull();
    expect(shortcutFor(press("A", { metaKey: true, shiftKey: true }), true)).toBe("approvals");
  });

  it("opens the commands with Cmd / or Cmd ?", () => {
    expect(shortcutFor(press("/", { metaKey: true }), true)).toBe("commands");
    expect(shortcutFor(press("?", { metaKey: true, shiftKey: true }), true)).toBe("commands");
  });

  it("ignores plain keys and Option", () => {
    expect(shortcutFor(press("n"), true)).toBeNull();
    expect(shortcutFor(press("n", { metaKey: true, altKey: true }), true)).toBeNull();
  });

  it("writes them for each platform", () => {
    expect(shortcutText("a", true, true)).toBe("⌘⇧A");
    expect(shortcutText("a", true, false)).toBe("Ctrl+Shift+A");
  });
});
