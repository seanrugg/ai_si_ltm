import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import Datasets, { AddDataset, DatasetDetail } from "./Datasets.jsx";
import { api } from "../api/client.js";
import { filterDatasets, formatBytes, sortDatasets, uploadFiles } from "../lib/datasets.js";

vi.mock("../api/client.js", () => ({ api: vi.fn() }));

const v = (version, status, extra = {}) => ({ version, status, item_count: 2, size_bytes: 2048, ...extra });
const DATASETS = [
  { id: "b", name: "Manuals", status: "approved", licence: "Internal", current_version: 1, versions: [v(1, "approved")] },
  { id: "c", name: "Old", status: "retired", licence: "Internal", current_version: 1, versions: [v(1, "approved")] },
  { id: "a", name: "Zeta", status: "pending", licence: "CC BY 4.0", current_version: null, versions: [v(1, "pending")] },
  { id: "d", name: "Glossary", status: "approved", licence: "CC0", current_version: 1,
    versions: [v(1, "approved"), v(2, "pending")] },
];

vi.mock("../api/useApi.js", () => ({
  useApi: vi.fn((path) => ({
    data: path === "/admin/datasets" ? { datasets: DATASETS } : { items: [] },
    error: null, loading: false, reload: () => {},
  })),
}));

afterEach(() => { cleanup(); api.mockReset(); });

describe("dataset ordering", () => {
  it("lists datasets with a pending version first and retired last", () => {
    expect(sortDatasets(DATASETS).map((d) => d.name)).toEqual(["Glossary", "Zeta", "Manuals", "Old"]);
  });

  it("filters pending and approved", () => {
    expect(filterDatasets(DATASETS, "pending").map((d) => d.name)).toEqual(["Zeta", "Glossary"]);
    expect(filterDatasets(DATASETS, "approved").map((d) => d.name)).toEqual(["Manuals", "Glossary"]);
  });

  it("renders the page in that order", () => {
    render(React.createElement(Datasets));
    const list = screen.getByRole("list", { name: "Datasets" });
    const names = [...list.querySelectorAll("button[aria-expanded]")].map((b) => b.textContent);
    expect(names).toEqual(["Glossary", "Zeta", "Manuals", "Old"]);
  });

  it("formats sizes", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(30 * 1024 * 1024)).toBe("30 MB");
  });
});

describe("create, upload, approve", () => {
  it("uploads one file per request and reports each result", async () => {
    api.mockResolvedValueOnce({ items: [{ key: "a.md" }] }).mockRejectedValueOnce(new Error("unsupported type"));
    const files = [new File(["# A"], "a.md"), new File(["x"], "b.bin")];
    const results = await uploadFiles(api, "ds1", 1, files);
    expect(api).toHaveBeenCalledTimes(2);
    expect(api.mock.calls[0][0]).toBe("/admin/datasets/ds1/versions/1/items");
    expect(api.mock.calls[0][1].method).toBe("POST");
    expect(api.mock.calls[0][1].body.getAll("files")).toHaveLength(1);
    expect(results).toEqual([
      { name: "a.md", ok: true, key: "a.md" },
      { name: "b.bin", ok: false, error: "unsupported type" },
    ]);
  });

  it("creates the dataset, then offers the upload into version 1", async () => {
    api.mockResolvedValueOnce({ id: "new-id", name: "Drive notes", versions: [v(1, "pending")] });
    render(React.createElement(AddDataset, { onCreated: () => {} }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Drive notes" } });
    fireEvent.change(screen.getByLabelText("Licence"), { target: { value: "Internal" } });
    fireEvent.click(screen.getByRole("button", { name: "Add dataset" }));
    await screen.findByText("Upload into Drive notes");
    expect(api).toHaveBeenCalledWith("/admin/datasets", { method: "POST", body: {
      name: "Drive notes", description: null, licence: "Internal", kind: "upload" } });
    expect(screen.getByLabelText("Files for version 1")).toBeDefined();
    expect(screen.getByText(/catalog integration/)).toBeDefined();
  });

  it("approves a pending version", async () => {
    api.mockResolvedValueOnce({});
    const onChanged = vi.fn();
    render(React.createElement(DatasetDetail, { dataset: DATASETS[2], onChanged }));
    fireEvent.click(screen.getByRole("button", { name: "Approve version 1" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(api).toHaveBeenCalledWith("/admin/datasets/a/versions/1/approve", { method: "POST" });
  });
});

describe("retire", () => {
  it("needs the dataset's name typed before it retires", async () => {
    api.mockResolvedValueOnce({});
    render(React.createElement(DatasetDetail, { dataset: DATASETS[0], onChanged: () => {} }));
    fireEvent.click(screen.getByRole("button", { name: "Retire" }));
    const go = screen.getByRole("button", { name: "Retire Manuals" });
    expect(go.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Confirm by typing Manuals"), { target: { value: "Manuals" } });
    expect(go.disabled).toBe(false);
    fireEvent.click(go);
    await waitFor(() => expect(api).toHaveBeenCalledWith("/admin/datasets/b/retire", { method: "POST" }));
  });

  it("offers a new version only when none is pending", () => {
    render(React.createElement(DatasetDetail, { dataset: DATASETS[0], onChanged: () => {} }));
    expect(screen.getByRole("button", { name: "New version" })).toBeDefined();
    cleanup();
    render(React.createElement(DatasetDetail, { dataset: DATASETS[3], onChanged: () => {} }));
    expect(screen.queryByRole("button", { name: "New version" })).toBeNull();
  });
});

describe("chunked upload (#245)", () => {
  it("sends big files in chunks with Content-Range and resumes after a failure", async () => {
    const { uploadChunked } = await import("../lib/datasets.js");
    const file = new File([new Uint8Array(25)], "run.h5");
    let failed = false;
    api.mockImplementation(async (path, opts = {}) => {
      if (path.endsWith("/uploads")) return { upload_id: "u1", chunk_bytes: 10, received_bytes: 0 };
      if (path.endsWith("/finish")) return { item: { key: "run.h5" } };
      if (opts.method === "PUT") {
        const [, first, last] = opts.headers["Content-Range"].match(/bytes (\d+)-(\d+)/);
        if (first === "10" && !failed) { failed = true; throw new Error("network"); }
        return { received_bytes: Number(last) + 1 };
      }
      return { received_bytes: 10 }; // status after the failure
    });
    const progress = [];
    const item = await uploadChunked(api, "d1", 1, file, (s, t) => progress.push([s, t]));
    expect(item.key).toBe("run.h5");
    const ranges = api.mock.calls.filter(([, o]) => o?.method === "PUT").map(([, o]) => o.headers["Content-Range"]);
    expect(ranges).toEqual(["bytes 0-9/25", "bytes 10-19/25", "bytes 10-19/25", "bytes 20-24/25"]);
    expect(progress.at(-1)).toEqual([25, 25]);
    expect(api.mock.calls.at(-1)[0]).toBe("/admin/datasets/d1/versions/1/items/uploads/u1/finish");
  });

  it("uses the chunked path only above 25 MB", async () => {
    const { CHUNKED_ABOVE } = await import("../lib/datasets.js");
    expect(CHUNKED_ABOVE).toBe(25 * 1024 * 1024);
  });
});

describe("archive uploads (#246)", () => {
  it("reports what an archive unpacked to", async () => {
    api.mockResolvedValueOnce({ items: [{ key: "bundle.zip", archive: {
      stored: [{ key: "a.md" }], skipped: [{ key: "n.zip", reason: "nested archive (zip)" }], errors: [] } }] });
    const [r] = await uploadFiles(api, "d1", 1, [new File(["PK"], "bundle.zip")]);
    expect(r.archive.stored).toHaveLength(1);
    expect(r.archive.skipped[0].reason).toMatch(/nested/);
  });
});
