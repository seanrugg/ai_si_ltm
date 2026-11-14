import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import SessionBoundary from "./SessionGuard.jsx";
import { Editor } from "../pages/Charter.jsx";
import { api } from "../api/client.js";
import { noteSession, resetSession } from "../lib/adminSession.js";

const h = React.createElement;

function json(status, body, headers = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

function Where() {
  return h("p", { "data-testid": "where" }, useLocation().pathname);
}

function renderApp() {
  return render(h(MemoryRouter, { initialEntries: ["/charter"] },
    h(SessionBoundary, { username: "admin", onSignedIn: () => {} },
      h(Routes, null, h(Route, { path: "/charter", element: h("div", null,
        h(Where), h(Editor, { initial: "# Old", graceDays: 14 })) })))));
}

let fetchMock;
beforeEach(() => {
  fetchMock = vi.fn(async () => json(200, { ok: true }));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  resetSession();
});

describe("session timeout (#378)", () => {
  it("shows the warning at idle minus warn, and Stay signed in posts activity", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    vi.setSystemTime(0);
    renderApp();
    act(() => noteSession({ idle: 3600, max: 12 * 3600, warn: 120 }, Date.now()));
    expect(screen.queryByRole("alert")).toBeNull();
    act(() => { vi.setSystemTime(3600_000 - 120_000); vi.advanceTimersByTime(1000); });
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toMatch(/signed out in 1:5\d because of inactivity/);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Stay signed in" })); });
    const call = fetchMock.mock.calls.find(([url]) => url === "/api/auth/admin/activity");
    expect(call).toBeDefined();
    expect(call[1].method).toBe("POST");
    expect(call[1].headers["X-LTMS-Activity"]).toBe("user");
  });

  it("on a 401, signs in over the page and keeps the route and the charter editor's text", async () => {
    renderApp();
    const editor = screen.getByLabelText(/charter/i, { selector: "textarea" });
    fireEvent.change(editor, { target: { value: "# Draft I have not saved" } });

    fetchMock.mockImplementationOnce(async () =>
      json(401, { error: { code: "not_logged_in", message: "admin session expired (inactivity)" } }));
    await act(async () => { await api("/admin/me").catch(() => {}); });

    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toMatch(/inactivity/);
    expect(screen.getByTestId("where").textContent).toBe("/charter");
    expect(screen.getByLabelText(/charter/i, { selector: "textarea" }).value).toBe("# Draft I have not saved");

    fetchMock.mockImplementationOnce(async () => json(200, { username: "admin" }));
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "pw" } });
    await act(async () => { fireEvent.submit(dialog); });

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByTestId("where").textContent).toBe("/charter");
    expect(screen.getByLabelText(/charter/i, { selector: "textarea" }).value).toBe("# Draft I have not saved");
  });

  it("marks requests right after a click as admin activity, background ones not", async () => {
    renderApp();
    await act(async () => { await api("/admin/me"); });
    expect(fetchMock.mock.calls.at(-1)[1].headers["X-LTMS-Activity"]).toBeUndefined();
    fireEvent.pointerDown(document.body);
    await act(async () => { await api("/admin/members"); });
    const last = fetchMock.mock.calls.filter(([url]) => url === "/api/admin/members").at(-1);
    expect(last[1].headers["X-LTMS-Activity"]).toBe("user");
  });
});
