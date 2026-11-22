import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import React from "react";
import NewTokenForm, { memberOptions } from "./NewTokenForm.jsx";
import { RECORD_TYPES, previewText } from "../lib/tokenScope.js";

afterEach(() => cleanup());

const members = [
  { name: "v3-c7r", kind: "robot", status: "active", cognition: null },
  { name: "jetson-hub", kind: "relay", status: "active" },
  { name: "claude-probe", kind: "assistant", status: "active" },
  { name: "gone", kind: "robot", status: "revoked" },
];
const swarms = [{ name: "echo-base", status: "active", members: [{ name: "v3-c7r", role: "member" }, { name: "jetson-hub", role: "hub" }] }];
const NOW = () => new Date(2026, 10, 21, 23, 0);

function renderForm() {
  const onCreate = vi.fn();
  render(React.createElement(NewTokenForm, { members, swarms, busy: false, error: null, onCreate, onCancel: () => {}, now: NOW }));
  return onCreate;
}
const name = (v) => fireEvent.change(screen.getByLabelText("Name (required)"), { target: { value: v } });
const tick = (label) => fireEvent.click(screen.getByRole("checkbox", { name: label }));
const create = () => fireEvent.click(screen.getByRole("button", { name: "Create token" }));

describe("New token form (#446)", () => {
  it("won't create a token without a record type, and says why", () => {
    const onCreate = renderForm();
    name("reader");
    create();
    expect(onCreate).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toMatch(/at least one record type/);
  });

  it("All types sends every type", () => {
    const onCreate = renderForm();
    name("everything");
    tick("All types");
    create();
    expect(onCreate.mock.calls[0][0].scope.types).toEqual(RECORD_TYPES);
    tick("All types");  // and clears them again
    expect(screen.getByText("None chosen yet.")).toBeTruthy();
  });

  it("members and swarms come from pickers; the preview matches the scope sent", () => {
    const onCreate = renderForm();
    name("vector-tuning");
    tick("perception");
    fireEvent.change(screen.getByRole("searchbox", { name: "Search members" }), { target: { value: "v3" } });
    const memberBox = screen.getByRole("group", { name: /Members/ });
    expect(within(memberBox).queryByText("claude-probe")).toBeNull();  // filtered out by the search
    fireEvent.click(within(memberBox).getByRole("checkbox", { name: /v3-c7r/ }));
    fireEvent.click(within(screen.getByRole("group", { name: /Swarms/ })).getByRole("checkbox", { name: "Free agents (no swarm)" }));
    tick("Include payloads");
    expect(screen.getByText("This token reads full record contents, not just headers. Use an expiry.")).toBeTruthy();
    expect(screen.getByLabelText("Expiry date").value).toBe("2026-11-28");  // 7 days by default
    const preview = screen.getByTestId("token-preview").textContent;
    create();
    const body = onCreate.mock.calls[0][0];
    expect(body).toEqual({ name: "vector-tuning", expires_at: "2026-11-28", owner: null, purpose: null,
      scope: { types: ["perception"], agents: ["v3-c7r"], swarms: [null], payloads: true } });
    expect(preview).toBe(previewText(body.scope, body.expires_at));
    expect(preview).toBe("Reads perception records of v3-c7r in free agents, with payloads, until 28 Nov 2026.");
  });

  it("expiry quick picks set the date, and Never clears it", () => {
    renderForm();
    fireEvent.click(screen.getByRole("button", { name: "30 days" }));
    expect(screen.getByLabelText("Expiry date").value).toBe("2026-12-21");
    expect(screen.getByRole("button", { name: "30 days" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Never" }));
    expect(screen.getByLabelText("Expiry date").value).toBe("");
  });

  it("every field has a labelled HelpTip (508)", () => {
    renderForm();
    for (const label of ["About the token name", "About the token owner", "About the token purpose",
      "About record types", "About limiting members", "About limiting swarms", "About payloads", "About token expiry"]) {
      expect(screen.getByRole("button", { name: label })).toBeTruthy();
    }
  });

  it("lists active members with kind and swarm", () => {
    expect(memberOptions(members, swarms)).toEqual([
      { value: "claude-probe", label: "claude-probe", detail: "AI agent · free agent" },
      { value: "jetson-hub", label: "jetson-hub", detail: "Hub · echo-base" },
      { value: "v3-c7r", label: "v3-c7r", detail: "Physical AI · echo-base" },
    ]);
  });
});
