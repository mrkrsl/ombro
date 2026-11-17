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
