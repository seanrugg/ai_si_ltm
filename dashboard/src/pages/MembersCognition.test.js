import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { CognitionControl, cognitionText } from "./Members.jsx";
import { api } from "../api/client.js";

vi.mock("../api/client.js", () => ({ api: vi.fn() }));
afterEach(() => cleanup());

describe("cognitionText", () => {
  it("names the value and its source", () => {
    expect(cognitionText(null)).toBe("Unknown");
    expect(cognitionText({ value: null, source: null })).toBe("Unknown");
    expect(cognitionText({ value: "offboard", source: "declared" })).toBe("Off board, declared by device");
    expect(cognitionText({ value: "offboard", source: "observed", offboard_share: 0.734 }))
      .toBe("Off board, observed: 73% of decisions");
    expect(cognitionText({ value: "onboard", source: "admin" })).toBe("On board, set by an admin");
  });
});

describe("CognitionControl", () => {
  it("applies an override, and No override clears it", async () => {
    vi.mocked(api).mockReset();
    vi.mocked(api).mockResolvedValue({});
    const onDone = vi.fn();
    const m = { id: "m1", name: "z3-r0", kind: "robot", cognition: { value: "onboard", source: "declared" } };
    render(React.createElement(CognitionControl, { m, onDone }));
    expect(screen.getByText("On board, declared by device")).toBeDefined();
    const select = screen.getByLabelText("AI location override for z3-r0");
    const apply = screen.getByRole("button", { name: "Apply" });
    expect(apply.disabled).toBe(true);
    fireEvent.change(select, { target: { value: "offboard" } });
    fireEvent.click(apply);
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(vi.mocked(api)).toHaveBeenCalledWith("/admin/members/m1/cognition",
      { method: "PATCH", body: { cognition: "offboard" } });

    cleanup();
    render(React.createElement(CognitionControl, {
      m: { ...m, cognition: { value: "offboard", source: "admin" } }, onDone }));
    fireEvent.change(screen.getByLabelText("AI location override for z3-r0"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(vi.mocked(api)).toHaveBeenLastCalledWith("/admin/members/m1/cognition",
      { method: "PATCH", body: { cognition: null } }));
  });
});
