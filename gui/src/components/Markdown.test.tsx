import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import css from "../app.css?raw";
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

describe("Markdown headings", () => {
  it("use one modest scale, largest first, none smaller than the text around them", () => {
    const size = (h: string) => {
      const rule = new RegExp(`\\.markdown ${h}(?:,[^{]*)?\\s*\\{[^}]*font-size:\\s*([\\d.]+)em`).exec(css);
      expect(rule, `a font-size for .markdown ${h}`).not.toBeNull();
      return Number(rule![1]);
    };
    const sizes = ["h1", "h2", "h3", "h4", "h5", "h6"].map(size);
    expect(sizes[0]).toBeLessThanOrEqual(1.4); // not the browser's 2em
    sizes.forEach((s, i) => {
      expect(s).toBeGreaterThanOrEqual(1);
      if (i) expect(s).toBeLessThanOrEqual(sizes[i - 1]);
    });
  });
});
