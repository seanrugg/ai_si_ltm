import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { GeoStatus, geoLine } from "./Settings.jsx";
import GeoCredit from "../components/GeoCredit.jsx";

afterEach(() => cleanup());

const OFF = { mode: "off", on: false, env: false, provider: null, attribution: false, file_kept: false,
  manual_url: "https://db-ip.com/db/download/ip-to-city-lite", credit_url: "https://db-ip.com" };
const DBIP = { ...OFF, mode: "download", on: true, provider: "DB-IP", licence: "CC BY 4.0", build_date: "2026-11-01",
  size: 130 * 1024 * 1024, attribution: true, file_kept: true, last_check: "2026-11-14T12:00:00Z",
  next_check: "2026-12-14T12:00:00Z", source: "https://download.db-ip.com/free/dbip-city-lite-2026-11.mmdb.gz" };

describe("location estimates panel (#388)", () => {
  it("says what is in use", () => {
    expect(geoLine({ enabled: false, reason: "no city database" }, OFF)).toBe("Off: no city database.");
    expect(geoLine(null, DBIP)).toBe("On: DB-IP city database, built 2026-11-01, 130 MB.");
    expect(geoLine(null, { ...OFF, env: true, env_path: "/opt/ltms/geo/city.mmdb" }))
      .toBe("On: set by the server's environment (LTMS_GEOIP_DB = /opt/ltms/geo/city.mmdb).");
  });

  it("offers to turn estimates on, and upload, when off", () => {
    render(React.createElement(GeoStatus, { geo: { enabled: false, reason: "no city database" }, src: OFF }));
    expect(screen.getByRole("button", { name: /Turn on network estimates/ })).toBeTruthy();
    expect(screen.getByRole("form", { name: "Upload a city database" })).toBeTruthy();
    expect(screen.queryByText("IP geolocation by DB-IP")).toBeNull();
  });

  it("credits DB-IP only when DB-IP's file is in use", () => {
    render(React.createElement(GeoStatus, { geo: { enabled: true }, src: DBIP }));
    const link = screen.getByText("IP geolocation by DB-IP");
    expect(link.getAttribute("href")).toBe("https://db-ip.com");
    cleanup();
    render(React.createElement(GeoCredit, { geo: { attribution: false } }));
    expect(screen.queryByText("IP geolocation by DB-IP")).toBeNull();
  });

  it("asks before deleting the file", () => {
    render(React.createElement(GeoStatus, { geo: { enabled: true }, src: DBIP }));
    fireEvent.click(screen.getByRole("button", { name: "Turn off and delete the file" }));
    expect(screen.getByRole("group", { name: "Confirm deletion" })).toBeTruthy();
  });

  it("turns its controls off when the environment sets the database", () => {
    render(React.createElement(GeoStatus, { geo: { enabled: true }, src: { ...OFF, env: true, env_path: "/x.mmdb" } }));
    expect(screen.queryByRole("button", { name: /Turn on/ })).toBeNull();
    expect(screen.queryByRole("form", { name: "Upload a city database" })).toBeNull();
  });

  it("the help explains the security point and the manual download", () => {
    render(React.createElement(GeoStatus, { geo: { enabled: false, reason: "x" }, src: OFF }));
    fireEvent.click(screen.getByRole("button", { name: "About location estimates" }));
    const text = document.body.textContent;
    expect(text).toMatch(/never sends members.? addresses/);
    expect(text).toContain("https://db-ip.com/db/download/ip-to-city-lite");
  });
});
