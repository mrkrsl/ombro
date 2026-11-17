// Where Ombro runs: the first question, the server form, and what the app says when it can't
// work with a server. The shell's commands are faked; all data is invented.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const where = vi.hoisted(() => ({
  useThisComputer: vi.fn(async () => undefined),
  pairOverSsh: vi.fn(async () => undefined),
  pairByAddress: vi.fn(async () => undefined),
}));
vi.mock("../where", async (real) => ({ ...(await real<typeof import("../where")>()), ...where }));

import { Connect } from "./Connect";

beforeEach(() => Object.values(where).forEach((f) => f.mockClear()));

describe("Connect", () => {
  it("asks where Ombro should run, and this computer needs nothing more", async () => {
    const onReady = vi.fn();
    render(<Connect onReady={onReady} />);
    expect(screen.getByRole("heading", { name: "Where should Ombro run?" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /On this computer/ }));
    expect(where.useThisComputer).toHaveBeenCalled();
    expect(onReady).toHaveBeenCalled();
  });

  it("pairs over SSH with the server as typed, and the port when given", async () => {
    const onReady = vi.fn();
    render(<Connect onReady={onReady} />);
    await userEvent.click(screen.getByRole("button", { name: /On a server/ }));
    const connect = screen.getByRole("button", { name: "Connect" });
    expect(connect).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Server"), "ada@box.invalid");
    await userEvent.click(connect);
    expect(where.pairOverSsh).toHaveBeenCalledWith("ada@box.invalid", undefined);
    expect(onReady).toHaveBeenCalled();
  });

  it("pairs by address with the code, and says why it couldn't", async () => {
    where.pairByAddress.mockRejectedValueOnce(new Error("That code isn't right, or it has run out."));
    const onReady = vi.fn();
    render(<Connect onReady={onReady} />);
    await userEvent.click(screen.getByRole("button", { name: /On a server/ }));
    await userEvent.click(screen.getByRole("button", { name: "By address" }));
    await userEvent.type(screen.getByLabelText("Address"), "100.101.102.103");
    await userEvent.type(screen.getByLabelText("Pairing code"), "abc-def-ghj");
    await userEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(where.pairByAddress).toHaveBeenCalledWith("100.101.102.103", "abc-def-ghj");
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("isn't right"));
    expect(onReady).not.toHaveBeenCalled();
  });

  it("when a server can't be reached, says why and offers this computer instead", async () => {
    const onReady = vi.fn();
    render(<Connect problem="box.invalid didn't answer." onReady={onReady} />);
    expect(screen.getByRole("heading", { name: "Ombro can't reach its server" })).toBeInTheDocument();
    expect(screen.getByText("box.invalid didn't answer.")).toBeInTheDocument();
    expect(screen.getByLabelText("Server")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Use Ombro on this computer instead" }));
    expect(where.useThisComputer).toHaveBeenCalled();
    expect(onReady).toHaveBeenCalled();
  });
});

describe("tooOld", () => {
  it("names the server's version", async () => {
    const { tooOld } = await import("../where");
    expect(tooOld("0.0.9")).toContain("Ombro 0.0.9");
  });
});
