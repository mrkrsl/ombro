// Where Ombro runs: the first question, the server form, and what the app says when it can't
// work with a server. The shell's commands are faked; all data is invented.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const where = vi.hoisted(() => ({
  useThisComputer: vi.fn(async () => undefined),
  pairOverSsh: vi.fn(async () => undefined),
  pairByAddress: vi.fn(async () => undefined),
  useKept: vi.fn(async (): Promise<"known" | "forgotten" | "none"> => "none"),
}));
vi.mock("../where", async (real) => ({ ...(await real<typeof import("../where")>()), ...where }));

import { useStore } from "../store/store";
import { Connect } from "./Connect";
import { WhereItRuns } from "./settings/General";
import type { Where } from "../where";

beforeEach(() => Object.values(where).forEach((f) => f.mockClear()));

const onMini: Where = { mode: "direct", host: null, port: null, address: "100.64.0.9:8765", local_home: true, started: true };
const localAfterMini: Where = {
  mode: "local", host: null, port: null, address: null, local_home: true, started: true,
  previous: { mode: "direct", host: null, port: null, address: "100.64.0.9:8765" },
};

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
    expect(where.useKept).toHaveBeenCalledWith({ host: "ada@box.invalid", port: undefined });
    expect(where.pairOverSsh).toHaveBeenCalledWith("ada@box.invalid", undefined, undefined);
    expect(onReady).toHaveBeenCalled();
  });

  it("over SSH, uses the key kept from before and doesn't pair again", async () => {
    where.useKept.mockResolvedValueOnce("known");
    const onReady = vi.fn();
    render(<Connect onReady={onReady} />);
    await userEvent.click(screen.getByRole("button", { name: /On a server/ }));
    await userEvent.type(screen.getByLabelText("Server"), "ada@box.invalid");
    await userEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(where.pairOverSsh).not.toHaveBeenCalled();
    expect(onReady).toHaveBeenCalled();
  });

  it("by address, a server this computer paired with before needs no code", async () => {
    where.useKept.mockResolvedValueOnce("known");
    const onReady = vi.fn();
    render(<Connect onReady={onReady} />);
    await userEvent.click(screen.getByRole("button", { name: /On a server/ }));
    await userEvent.click(screen.getByRole("button", { name: "By address" }));
    await userEvent.type(screen.getByLabelText("Address"), "100.64.0.9");
    await userEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(where.useKept).toHaveBeenCalledWith({ address: "100.64.0.9" });
    expect(where.pairByAddress).not.toHaveBeenCalled();
    expect(onReady).toHaveBeenCalled();
  });

  it("by address with no code, asks for one when there's no key or the server forgot it", async () => {
    const onReady = vi.fn();
    render(<Connect onReady={onReady} />);
    await userEvent.click(screen.getByRole("button", { name: /On a server/ }));
    await userEvent.click(screen.getByRole("button", { name: "By address" }));
    await userEvent.type(screen.getByLabelText("Address"), "100.64.0.9");
    await userEvent.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("hasn't paired with 100.64.0.9 yet"));
    where.useKept.mockResolvedValueOnce("forgotten");
    await userEvent.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("no longer knows this computer"));
    expect(where.pairByAddress).not.toHaveBeenCalled();
    expect(onReady).not.toHaveBeenCalled();
  });

  it("pairs over SSH with Docker's port and code when given", async () => {
    render(<Connect onReady={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /On a server/ }));
    await userEvent.type(screen.getByLabelText("Server"), "ada@box.invalid");
    await userEvent.click(screen.getByText("Another port, or Ombro in Docker there"));
    await userEvent.type(screen.getByLabelText("Port"), "8766");
    await userEvent.type(screen.getByLabelText("SSH pairing code"), " bsvw ");
    await userEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(where.pairOverSsh).toHaveBeenCalledWith("ada@box.invalid", 8766, "bsvw");
  });

  it("pairs by address with the code, and says why it couldn't", async () => {
    where.pairByAddress.mockRejectedValueOnce(new Error("That code isn't right, or it has run out."));
    const onReady = vi.fn();
    render(<Connect onReady={onReady} />);
    await userEvent.click(screen.getByRole("button", { name: /On a server/ }));
    await userEvent.click(screen.getByRole("button", { name: "By address" }));
    await userEvent.type(screen.getByLabelText("Address"), "100.101.102.103");
    await userEvent.type(screen.getByLabelText("Pairing code"), "34fa");
    await userEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(where.pairByAddress).toHaveBeenCalledWith("100.101.102.103", "34fa");
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

describe("reconnecting", () => {
  it("starts from the server used last", () => {
    useStore.setState({ where: { mode: "direct", host: null, port: null, address: "127.0.0.1:8799", local_home: false, started: true } });
    render(<Connect problem="The server has forgotten its connection with this computer." onReady={vi.fn()} />);
    expect(screen.getByRole("button", { name: "By address", pressed: true })).toBeInTheDocument();
    expect(screen.getByLabelText("Address")).toHaveValue("127.0.0.1:8799");
    useStore.setState({ where: null });
  });
});

describe("Settings › Where Ombro runs", () => {
  it("on this computer, offers the server used before, and reconnects with no code", async () => {
    const connectNow = vi.fn(async () => undefined);
    useStore.setState({ where: localAfterMini, connectNow });
    where.useKept.mockResolvedValueOnce("known");
    render(<WhereItRuns />);
    await userEvent.click(screen.getByRole("button", { name: "Reconnect to 100.64.0.9:8765" }));
    expect(where.useKept).toHaveBeenCalledWith({ address: "100.64.0.9:8765" });
    await waitFor(() => expect(connectNow).toHaveBeenCalled());
    expect(where.pairByAddress).not.toHaveBeenCalled();
    useStore.setState({ where: null });
  });

  it("when the server has forgotten this computer, asks for a code there", async () => {
    const connectNow = vi.fn(async () => undefined);
    useStore.setState({ where: localAfterMini, connectNow });
    where.useKept.mockResolvedValueOnce("forgotten");
    render(<WhereItRuns />);
    await userEvent.click(screen.getByRole("button", { name: "Reconnect to 100.64.0.9:8765" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("100.64.0.9:8765 no longer knows this computer"));
    expect(screen.getByLabelText("Address")).toHaveValue("100.64.0.9:8765");
    expect(screen.getByLabelText("Pairing code")).toBeInTheDocument();
    expect(connectNow).not.toHaveBeenCalled();
    useStore.setState({ where: null });
  });

  it("on a server, there's nothing to reconnect to", () => {
    useStore.setState({ where: onMini });
    render(<WhereItRuns />);
    expect(screen.queryByRole("button", { name: /Reconnect/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use this computer instead" })).toBeInTheDocument();
    useStore.setState({ where: null });
  });
});

describe("tooOld", () => {
  it("names the server's version", async () => {
    const { tooOld } = await import("../where");
    expect(tooOld("0.0.9")).toContain("Ombro 0.0.9");
  });
});
