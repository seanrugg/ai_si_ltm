import { useCallback, useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useAdmin } from "../App.jsx";
import { api } from "../api/client.js";
import { THEMES, getTheme, setTheme } from "../lib/theme.js";
import {
  GROUPS, OVERVIEW, dismissChecklist, getDirectivesSeen, groupBadge, groupOf, groupOpen, isChecklistDismissed,
  itemBadge, loadGroupChoices, saveGroupChoices, setDirectivesSeen, setupDone,
} from "../lib/nav.js";
import GetStarted from "./GetStarted.jsx";
import HelpTip from "./HelpTip.jsx";

// Same interval as the Overview page's live refresh.
export const NAV_POLL_MS = 30_000;

// The text says what the number means; the number or "!" is visual.
function Badge({ badge }) {
  if (!badge) return null;
  return (
    <span data-testid="nav-badge">
      <span aria-hidden="true"
            className={badge.warn
              ? "rounded-sm border border-warn px-1.5 text-xs font-bold text-warn"
              : "rounded-full bg-led px-1.5 text-xs font-bold text-ink"}>
        {badge.warn ? "!" : badge.count}
      </span>
      <span className="sr-only">{badge.text}</span>
    </span>
  );
}

function Item({ item, status, onPick }) {
  return (
    <li>
      <NavLink
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
        <Badge badge={itemBadge(item, status)} />
      </NavLink>
    </li>
  );
}

const HEADER = "text-xs font-bold uppercase tracking-wide text-paper-muted";

function Group({ group, status, open, onToggle, onPick }) {
  const listId = `nav-${group.key}`;
  return (
    <div className="mt-3">
      <div className="flex items-center px-3 py-1">
        {group.collapsible ? (
          <button type="button" aria-expanded={open} aria-controls={listId} onClick={onToggle}
                  className={`flex flex-1 items-center justify-between gap-2 text-left hover:text-paper ${HEADER}`}>
            <span>
              <span aria-hidden="true" className="mr-1 inline-block w-3">{open ? "▾" : "▸"}</span>
              {group.label}
            </span>
            {!open && <Badge badge={groupBadge(group, status)} />}
          </button>
        ) : (
          <h2 id={`${listId}-h`} className={`flex-1 ${HEADER}`}>{group.label}</h2>
        )}
        <HelpTip id={`nav-help-${group.key}`} label={`About ${group.label}`}>{group.help}</HelpTip>
      </div>
      <ul id={listId} hidden={!open} aria-labelledby={group.collapsible ? undefined : `${listId}-h`}
          aria-label={group.collapsible ? group.label : undefined} className={open ? "flex flex-col gap-0.5" : "hidden"}>
        {group.items.map((item) => <Item key={item.to} item={item} status={status} onPick={onPick} />)}
      </ul>
    </div>
  );
}

/** Badge counts and setup progress, polled in the background (not admin activity, #378). */
function useNavStatus(pathname) {
  const [status, setStatus] = useState(null);
  const onDirectives = pathname === "/directives";
  const load = useCallback(async () => {
    try {
      const s = await api("/admin/nav-status", { params: { directives_seen: getDirectivesSeen() }, activity: false });
      // Looking at Directives counts as seeing its declines; remember the server's time.
      if (onDirectives && s?.as_of) {
        setDirectivesSeen(s.as_of);
        setStatus({ ...s, directive_declines: 0 });
      } else {
        setStatus(s);
      }
    } catch {
      // keep the last counts; a 401 is handled by the session guard
    }
  }, [onDirectives]);

  useEffect(() => {
    load();
  }, [load, pathname]);
  useEffect(() => {
    const id = setInterval(load, NAV_POLL_MS);
    return () => clearInterval(id);
  }, [load]);
  return status;
}

export function Nav({ status, onPick }) {
  const { pathname } = useLocation();
  const [choices, setChoices] = useState(loadGroupChoices);
  // Groups opened because the current page is in them; not remembered.
  const [auto, setAuto] = useState(() => new Set());
  const [dismissed, setDismissed] = useState(isChecklistDismissed);
  const done = setupDone(status?.setup);

  const isOpen = (g) => !g.collapsible || auto.has(g.key) || groupOpen(g.key, choices, done);

  const current = groupOf(pathname);
  useEffect(() => {
    const g = GROUPS.find((x) => x.key === current);
    if (g && g.collapsible && !groupOpen(g.key, choices, done)) {
      setAuto((s) => (s.has(g.key) ? s : new Set(s).add(g.key)));
    }
    // Only on navigation: collapsing the current group by hand must stick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  function toggle(g) {
    const next = { ...choices, [g.key]: !isOpen(g) };
    setChoices(next);
    saveGroupChoices(next);
    setAuto((s) => {
      const n = new Set(s);
      n.delete(g.key);
      return n;
    });
  }

  return (
    <nav aria-label="Main" className="flex flex-col">
      <ul className="mb-1 flex flex-col gap-0.5">
        <Item item={OVERVIEW} status={status} onPick={onPick} />
      </ul>
      {done === false && !dismissed && (
        <GetStarted setup={status.setup} onPick={onPick}
                    onDismiss={() => { dismissChecklist(); setDismissed(true); }} />
      )}
      {GROUPS.map((g) => (
        <Group key={g.key} group={g} status={status} open={isOpen(g)} onToggle={() => toggle(g)} onPick={onPick} />
      ))}
    </nav>
  );
}

function ThemeSelect() {
  const [theme, setCurrent] = useState(getTheme);
  return (
    <label className="flex items-center gap-2">
      Theme
      <select className="field py-1" value={theme} onChange={(e) => setCurrent(setTheme(e.target.value))}>
        {THEMES.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
      </select>
    </label>
  );
}

export default function Shell({ children }) {
  const { username, logout } = useAdmin();
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();
  // Re-checked on every navigation (so a badge clears after an approval) and every 30 s.
  const status = useNavStatus(location.pathname);

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
          <Nav status={status} onPick={() => setMenuOpen(false)} />
          <div className="mt-6 border-t border-ink-line px-3 pt-4 text-sm text-paper-muted">
            <ThemeSelect />
            <p className="mt-3">Signed in as {username}</p>
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
