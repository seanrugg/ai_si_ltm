import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";
import { EndpointForm } from "./Lrs.jsx";

// The LRS (add/edit endpoint) form is a credential form outside the login page:
// browsers must not autofill the admin login into it.
const html = renderToStaticMarkup(
  React.createElement(EndpointForm, {
    initial: null,
    onSaved: () => {},
    onCancel: () => {},
    busy: false,
  })
);

describe("LRS endpoint form autofill attributes", () => {
  it("wraps the form so it is not detected as a login form", () => {
    expect(html).toContain('<form autoComplete="off"');
  });

  it("gives the username input autocomplete off and a distinct name", () => {
    const input = html.match(/<input[^>]*?name="lrs-username"[^>]*?>/);
    expect(input).not.toBeNull();
    expect(input[0]).toContain('autoComplete="off"');
  });

  it("marks the password input autocomplete new-password", () => {
    const input = html.match(/<input[^>]*?type="password"[^>]*?>/);
    expect(input).not.toBeNull();
    expect(input[0]).toContain('autoComplete="new-password"');
  });
});
