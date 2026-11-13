import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import GuidedSetup, { setupState } from "./GuidedSetup.jsx";
import { api } from "../api/client.js";

vi.mock("../api/client.js", () => ({ api: vi.fn(async () => ({})) }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

const hub = { id: "1", name: "jetson-hub", public_key: "b".repeat(64), intended: { swarm: "echo-base", role: "hub" } };
const swarm = (members = []) => ({ id: "a".repeat(64), name: "echo-base", status: "active", members });

function view(props) {
  return render(React.createElement(MemoryRouter, null,
    React.createElement(GuidedSetup, { member: hub, allMembers: [{ ...hub, status: "active" }], onReload: () => {}, onFinish: () => {}, ...props })));
}

describe("guided setup after Approve (#313)", () => {
  it("works out each step from the swarm list", () => {
    expect(setupState(hub, [])).toMatchObject({ swarm: null, step2: "todo", step3: "todo", joinRole: "member" });
    expect(setupState(hub, [swarm([{ name: "jetson-hub", role: "member" }])])).toMatchObject({ step2: "done", step3: "todo" });
    expect(setupState(hub, [swarm([{ name: "jetson-hub", role: "hub" }])])).toMatchObject({ step2: "done", step3: "done" });
    expect(setupState({ ...hub, intended: { swarm: "echo-base", role: "observer" } }, []).step3).toBeNull();
  });

  it("offers to create a missing swarm with its name filled in", () => {
    view({ swarms: [] });
    expect(screen.getByText(/doesn't exist yet/)).toBeTruthy();
    expect(screen.getByDisplayValue("echo-base")).toBeTruthy();
  });

  it("adds to the swarm only on a click, as member first for a hub, and can wait", () => {
    view({ swarms: [swarm()] });
    expect(api).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Add to echo-base" }));
    expect(api).toHaveBeenCalledWith("/admin/swarms/echo-base/members", { method: "POST", body: { member: "jetson-hub", role: "member" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Do this later" })[0]);
    expect(screen.getByText(/left for later/)).toBeTruthy();
  });

  it("then offers to appoint it as hub with the root key", () => {
    view({ swarms: [swarm([{ name: "jetson-hub", role: "member" }])] });
    expect(screen.getByText(/Step 2: Add to echo-base as member/).textContent).toContain("(done)");
    expect(document.querySelector('input[name="swarm-root-key"]').type).toBe("password");
  });

  it("says when it already joined with an invite", () => {
    view({ swarms: [swarm([{ name: "jetson-hub", role: "member" }])], joinedEarly: true });
    expect(screen.getByText("Joined echo-base as member with an invite.")).toBeTruthy();
  });
});
