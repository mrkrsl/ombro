// A new chat is listed from its first message, and clicking it again goes back to the live session.
import { describe, expect, it, vi } from "vitest";
import type { Client } from "../api/serve";
import type { ServerMessage } from "../api/types";
import { newConversation } from "./conversation";
import { useStore } from "./store";

describe("a new chat whose Lead is still answering", () => {
  it("joins the list when serve says it is saved, and clicking it goes back to the same session", async () => {
    const row = { name: "invented-garden-survey-20261119-1300", title: "Invented garden survey" };
    const get = vi.fn(async (path: string) => (path === "/chats" ? [row] : { chat: row.name, title: row.title }));
    const post = vi.fn(async () => ({ session: { id: "other" } }));
    useStore.setState({
      api: { get, post } as unknown as Client,
      conversations: { s1: { ...newConversation("s1"), state: "working" } },
      current: "s1",
      chats: [],
      chatOn: null,
      screen: "chat",
    });

    useStore.getState().receive({ type: "chat_saved", session: "s1", seq: 3, chat: row.name } as ServerMessage);
    expect(useStore.getState().chatOn).toBe(row.name);
    await vi.waitFor(() => expect(useStore.getState().chats).toEqual([row]));

    useStore.setState({ current: null, screen: "jobs" }); // looked at something else meanwhile
    await useStore.getState().openChat(row.name);
    expect(useStore.getState().current).toBe("s1"); // the live session, not a second one on the same file
    expect(post).not.toHaveBeenCalled();
  });
});
