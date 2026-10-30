import { useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useAdmin } from "../App.jsx";
import { useApi } from "../api/useApi.js";

const NAV = [
  { to: "/", label: "Timeline", end: true },
  { to: "/members", label: "Members", badge: "pending" },
  { to: "/integrity", label: "Integrity" },
  { to: "/volume", label: "Volume" },
  { to: "/swarms", label: "Swarms" },
  { to: "/conversation", label: "Conversation" },
  { to: "/outcomes", label: "Outcomes" },
  { to: "/lessons", label: "Lessons" },
  { to: "/recall-log", label: "Recall log" },
  { to: "/settings", label: "Settings" },
];

function Nav({ pending, onPick }) {
  return (
    <nav aria-label="Main" className="flex flex-col gap-0.5">
      {NAV.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          onClick={onPick}
          className={({ isActive }) =>
            `flex items-center justify-between rounded-md px-3 py-2 text-sm ${
              isActive ? "bg-ink-hover font-bold text-paper" : "text-paper-muted hover:bg-ink-hover hover:text-paper"
            }`
          }
        >
          {item.label}
          {item.badge === "pending" && pending > 0 && (
            <span className="rounded-full bg-led px-1.5 text-xs font-bold text-ink" aria-label={`${pending} waiting`}>
              {pending}
            </span>
          )}
        </NavLink>
      ))}
    </nav>
  );
}

export default function Shell({ children }) {
  const { username, logout } = useAdmin();
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();
  // Re-checked on every navigation so the badge clears after an approval.
  const pending = useApi("/admin/members", { status: "pending", _: location.key }).data?.members?.length || 0;

  return (
    <div className="min-h-screen md:grid md:grid-cols-[13rem_1fr]">
      <aside className="border-b border-ink-line bg-ink-raised md:min-h-screen md:border-b-0 md:border-r">
        <div className="flex items-center justify-between px-4 py-4">
          <span className="text-lg font-bold">
            LTMS<span className="ml-2 inline-block h-2 w-2 rounded-full bg-led align-middle" aria-hidden="true" />
          </span>
          <button className="btn md:hidden" aria-expanded={menuOpen} onClick={() => setMenuOpen((o) => !o)}>
            Menu
          </button>
        </div>
        <div className={`${menuOpen ? "block" : "hidden"} px-2 pb-4 md:block`}>
          <Nav pending={pending} onPick={() => setMenuOpen(false)} />
          <div className="mt-6 border-t border-ink-line px-3 pt-4 text-sm text-paper-muted">
            <p>Signed in as {username}</p>
            <button className="mt-2 underline hover:text-paper" onClick={logout}>
              Sign out
            </button>
          </div>
        </div>
      </aside>
      <main className="min-w-0 px-4 py-6 md:px-8">{children}</main>
    </div>
  );
}
