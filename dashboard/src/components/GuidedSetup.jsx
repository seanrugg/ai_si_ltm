// After Approve (#313): continue into swarm setup for a member that said where it's meant to go.
// Step 2: add it to its intended swarm (creating the swarm first if needed).
// Step 3 (hub only): appoint it as hub, signing with the root key in the browser.
// Every step needs a click, and can be left for later.
import { useState } from "react";
import { api } from "../api/client.js";
import { CreateSwarm } from "../pages/Swarms.jsx";
import { AppointHub } from "../pages/profile/Manage.jsx";
import { ErrorNote } from "./ui.jsx";
import HelpTip from "./HelpTip.jsx";
import { GUIDED_SETUP } from "../lib/help.js";

/** Where a member stands against its intent: {swarm, placement, step2, step3}. */
export function setupState(member, swarms) {
  const want = member?.intended || {};
  const swarm = (swarms || []).find((s) => s.name === want.swarm) || null;
  const placement = swarm ? (swarm.members || []).find((x) => x.name === member.name) : null;
  const joinRole = want.role === "hub" ? "member" : want.role || "member";
  return {
    swarm, placement, joinRole,
    step2: placement ? "done" : "todo",
    step3: want.role !== "hub" ? null : placement?.role === "hub" ? "done" : "todo",
  };
}

function Step({ n, title, status, children }) {
  return (
    <li className="mt-3 rounded-md border border-ink-line p-3">
      <p className="text-sm font-bold">
        Step {n}: {title} <span className="ml-2 font-normal text-paper-muted">({status === "done" ? "done" : status === "later" ? "left for later" : "not done"})</span>
      </p>
      {status === "todo" && children}
    </li>
  );
}

export default function GuidedSetup({ member, swarms, allMembers, joinedEarly, onReload, onFinish }) {
  const [later, setLater] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const st = setupState(member, swarms);
  const want = member.intended;

  async function act(fn) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onReload?.();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const status = (key) => (later[key] ? "later" : st[key]);
  return (
    <section className="panel mb-4 p-4" aria-labelledby={`setup-${member.name}-h`}>
      <h3 id={`setup-${member.name}-h`} className="flex items-center gap-2 font-bold">
        {member.name} is approved. Set it up in {want.swarm}?
        <HelpTip id={`setup-help-${member.name}`} label="About guided setup">{GUIDED_SETUP}</HelpTip>
      </h3>
      <ErrorNote error={error} />
      <ol>
        <Step n={2} title={`Add to ${want.swarm} as ${st.joinRole}`} status={status("step2")}>
          {joinedEarly ? null : !st.swarm ? (
            <>
              <p className="text-sm text-paper-muted">{want.swarm} doesn&apos;t exist yet. Create it first:</p>
              <CreateSwarm initialName={want.swarm} onCreated={onReload} />
            </>
          ) : (
            <span className="mt-2 flex flex-wrap gap-2">
              <button type="button" className="btn btn-primary" disabled={busy}
                      onClick={() => act(() => api(`/admin/swarms/${encodeURIComponent(want.swarm)}/members`,
                        { method: "POST", body: { member: member.name, role: st.joinRole } }))}>
                Add to {want.swarm}
              </button>
              <button type="button" className="btn" onClick={() => setLater({ ...later, step2: true })}>Do this later</button>
            </span>
          )}
        </Step>
        {st.step2 === "done" && joinedEarly && (
          <li className="mt-1 text-sm text-paper-muted">Joined {want.swarm} as {st.placement.role} with an invite.</li>
        )}
        {st.step3 !== null && (
          <Step n={3} title={`Appoint as hub of ${want.swarm}`} status={st.step2 === "done" ? status("step3") : "not ready"}>
            <AppointHub swarm={st.swarm} allMembers={allMembers} busy={busy} act={act} />
            <button type="button" className="btn" onClick={() => setLater({ ...later, step3: true })}>Do this later</button>
          </Step>
        )}
      </ol>
      <button type="button" className="btn mt-3" onClick={onFinish}>Finish</button>
    </section>
  );
}
