import { describe, expect, it } from "vitest";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import Markdown from "../components/Markdown.jsx";
import { parseInline, parseMarkdown, safeHref } from "./markdown.js";

const html = (src) => renderToStaticMarkup(React.createElement(Markdown, { source: src }));

describe("safe markdown", () => {
  it("escapes HTML, including <script>", () => {
    const out = html("# Hi <b>there</b>\n\n<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>");
    expect(out).not.toContain("<script");
    expect(out).not.toContain("<img");
    expect(out).not.toContain("<b>");
    expect(out).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
  });

  it("drops javascript:, data: and other non-http links but keeps the words", () => {
    for (const bad of ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,x", "vbscript:x", "//evil.example", "java\tscript:x"]) {
      const out = html(`[click](${bad})`);
      expect(out, bad).not.toContain("<a");
      expect(out).toContain("click");
    }
    expect(safeHref("https://example.org/a")).toBe("https://example.org/a");
    expect(safeHref("#article-3")).toBe("#article-3");
    expect(safeHref("docs/charter.md")).toBe("docs/charter.md");
  });

  it("renders external links safely", () => {
    const out = html("[LTMS](https://example.org)");
    expect(out).toContain('href="https://example.org"');
    expect(out).toContain('rel="noopener noreferrer nofollow"');
  });

  it("parses the blocks a charter uses", () => {
    const src = "# Charter\n\n## Article 1\n\nHumans **stay** in *charge*.\n\n- one\n- two\n\n1. first\n2. second\n\n"
      + "> quoted\n\n```\ncode <x>\n```\n\n---\n\n| A | B |\n|---|---|\n| 1 | `2` |\n";
    const kinds = parseMarkdown(src).map((b) => b.t);
    expect(kinds).toEqual(["h", "h", "p", "ul", "ol", "quote", "code", "hr", "table"]);
    const out = html(src);
    expect(out).toContain("<strong>stay</strong>");
    expect(out).toContain("<em>charge</em>");
    expect(out).toContain("code &lt;x&gt;");
    expect(out).toContain("<th");
  });

  it("handles inline code and escapes", () => {
    expect(parseInline("`a*b*` \\*not em\\*")).toEqual([
      { t: "code", v: "a*b*" }, { t: "text", v: " *not em*" }]);
  });
});
