import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import React from "react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { PAGE_ROUTES } from "./App.jsx";

// Pages load data on mount; never answer, so only routing is under test.
vi.mock("./api/client.js", () => ({
  api: vi.fn(() => new Promise(() => {})),
  ApiError: class extends Error {},
  setUnauthorizedHandler: () => {},
}));
afterEach(() => cleanup());

function go(url) {
  const router = createMemoryRouter(PAGE_ROUTES, { initialEntries: [url] });
  render(React.createElement(RouterProvider, { router }));
  return router;
}

describe("home and Timeline routes (#379)", () => {
  it("/ is Overview", async () => {
    const router = go("/");
    await waitFor(() => expect(router.state.location.pathname).toBe("/activity"));
  });

  it("old Timeline links with a query string go to /timeline", async () => {
    let router = go("/?agent=alice");
    await waitFor(() => expect(router.state.location.pathname).toBe("/timeline"));
    expect(router.state.location.search).toBe("?agent=alice");
    cleanup();
    router = go("/?swarm=test-swarm&type=event");
    await waitFor(() => expect(router.state.location.pathname).toBe("/timeline"));
    expect(router.state.location.search).toBe("?swarm=test-swarm&type=event");
  });

  it("/overview is an alias for Overview, and renamed pages keep their URLs", async () => {
    const router = go("/overview");
    await waitFor(() => expect(router.state.location.pathname).toBe("/activity"));
    const paths = PAGE_ROUTES.map((r) => r.path);
    for (const p of ["/timeline", "/access", "/lrs", "/lessons", "/retention", "/activity"]) {
      expect(paths).toContain(p);
    }
  });
});
