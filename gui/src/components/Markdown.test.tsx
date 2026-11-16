import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownFile, splitFrontMatter } from "./Markdown";

describe("MarkdownFile", () => {
  it("splits the settings header from the body", () => {
    expect(splitFrontMatter("---\nname: Ana\n---\n# Hi\n")).toEqual({ settings: "name: Ana", body: "# Hi" });
    expect(splitFrontMatter("# Hi")).toEqual({ settings: "", body: "# Hi" });
  });

  it("shows settings as a list and the body formatted", () => {
    render(<MarkdownFile text={"---\nmodel: deepseek-pro\n---\n# The brief\n\n**Bold** text"} />);
    expect(screen.getByRole("heading", { name: "The brief" })).toBeInTheDocument();
    expect(screen.getByText("Bold").tagName).toBe("STRONG");
    expect(screen.getByText("model:")).toBeInTheDocument();
  });
});
