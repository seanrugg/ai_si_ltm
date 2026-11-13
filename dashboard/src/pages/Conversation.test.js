import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import Conversation, { authorChipClass } from "./Conversation.jsx";

describe("Conversation view switch", () => {
  it("defaults to the Swarm view", () => {
    const html = renderToStaticMarkup(
      React.createElement(MemoryRouter, null, React.createElement(Conversation))
    );
    // two view radios, Swarm checked by default
    expect(html).toContain('<input type="radio" name="view" checked="" value="swarm"/>');
    expect(html).toContain('<input type="radio" name="view" value="members"/>');
    expect((html.match(/checked=""/g) || []).length).toBe(1);
  });
});

describe("author filter chip", () => {
  it("keeps the selected name readable (no solid fill in the text colour)", () => {
    const on = authorChipClass(true).split(/\s+/);
    expect(on).toContain("text-led");
    expect(on).not.toContain("bg-led");
    expect(on).toContain("bg-led/20");
  });
  it("is muted when not selected", () => {
    expect(authorChipClass(false)).toContain("text-paper-muted");
  });
});
