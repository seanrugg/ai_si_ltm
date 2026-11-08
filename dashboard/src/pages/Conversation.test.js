import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import Conversation from "./Conversation.jsx";

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
