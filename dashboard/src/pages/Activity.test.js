import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import Activity from "./Activity.jsx";
import { useApi } from "../api/useApi.js";

// Mirrors the mock pattern in Members.test.js: replace useApi with a fake so the
// page renders without a server and we can inspect the call it makes.
vi.mock("../api/useApi.js", () => ({
  useApi: vi.fn(() => ({
    data: { groups: [], since: null, bucket_seconds: 300 },
    error: null,
    loading: false,
    reload: () => {},
  })),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function renderAt(url) {
  return render(React.createElement(MemoryRouter, { initialEntries: [url] }, React.createElement(Activity)));
}

describe("Activity page (#426)", () => {
  it("sends the hours query as top-level useApi params", async () => {
    renderAt("/activity?hours=168");
    await waitFor(() => expect(useApi).toHaveBeenCalledWith("/admin/activity", { hours: 168 }));
  });
});
