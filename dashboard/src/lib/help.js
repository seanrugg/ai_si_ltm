// In-place help for admin controls (issue #178). All wording lives here so it
// can be reviewed and edited in one place. Plain text, at most two sentences.

export const LRS_PAUSE =
  "While paused, new records aren't sent to this LRS, now or later. Records already sent stay in the LRS.";
export const LRS_SCOPE = "Which records this LRS receives. A blank Types field sends nothing.";
export const RETENTION_RULES =
  "Deletes payloads past their age at the nightly run. Headers stay, so chains still verify.";
export const RETENTION_FOREVER = "Nothing expires. Plan storage accordingly.";
export const EXPORT_FORMAT =
  "JSONL is a complete copy that can be verified with the members' keys. CSV opens in a spreadsheet but can't be verified.";
export const SWARM_MOVE =
  "Moving a member changes which swarm its new records belong to. Its past records keep the swarm they were written in.";
export const SWARM_ROLE =
  "A role labels a member within its swarm. Only the hub role grants anything (relaying). Hubs are appointed with the swarm's root key.";
export const RECORD_DELETE =
  "Deletes this one record for good, leaving a tombstone so the chain still verifies. Use it to remove specific records, such as test data.";
export const MEMORY_WIPE =
  "Gives the droid a clean slate: it stops recalling anything from before now, from any member. Nothing is deleted, policies still apply, and it can't be undone.";
export const WIPE_VS_DELETE = "Wipe changes what a droid remembers; delete removes records from the log.";
