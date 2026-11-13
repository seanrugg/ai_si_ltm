// In-place help for admin controls (issue #178). All wording lives here so it
// can be reviewed and edited in one place. Plain text, at most two sentences.

export const LRS_PAUSE =
  "While paused, new records aren't sent to this LRS, now or later. Records already sent stay in the LRS.";
export const LRS_SEND_READS = "Also send every dataset read (who read which item, and when) as a read-resource statement. Scope filters don't apply to reads. Reads made while this is off are never sent.";
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
export const ADD_SWARM =
  "Creates the swarm and its root key pair in your browser. The public key goes to LTMS; the private key is shown once, next.";
export const ROOT_PRIVATE_KEY =
  "The private root key signs hub appointments for this swarm. It never leaves this page. LTMS can't recover it, and anyone who has it can appoint a hub.";
export const ROOT_PUBLIC_KEY =
  "LTMS keeps only the public key. It checks every hub appointment against it. The swarm's ID is a hash of this public key.";


export const SWARM_RENAME =
  "Renaming keeps the swarm's ID, keys and records. Its MQTT topics change: update the hub's and every member's swarm name, and the broker ACL, or they stop connecting.";
export const SWARM_REMOVE =
  "Removing hides the swarm and frees its name for reuse. Its history stays: records, audit and the swarm's ID are kept unless you purge records.";
export const SWARM_REMOVE_MEMBERS =
  "Free agents keep working outside any swarm. Removed members are revoked: they can't send records until they enroll again.";
export const SWARM_REMOVE_RECORDS =
  "Purge deletes only records that came through this swarm, leaving tombstones. Members' other records stay.";

// Datasets (#242)
export const DATASET_APPROVE = "Approved data is readable by every member of the fleet. Check its contents and licence first.";
export const DATASET_SCOPE = "Every dataset is shared with the whole fleet.";
export const DATASET_LARGE = "Large files served to many members at once slow LTMS down. For large or frequently read data, add a catalog integration instead.";
export const INTEGRATION_ABOUT = "A pointer to data or models that stay where they are (MCP, Hugging Face, S3). LTMS never fetches from it: members connect themselves and report what they read. To copy content into LTMS, use Reference sources.";
export const REFERENCE_SOURCES = "Material LTMS can import itself, one item per entry, rights kept. Import fetches from the source's own site; on a closed network, use a saved copy. Each import is a pending version you approve.";
export const REFERENCE_REFRESH = "After the first import, LTMS checks the source on this schedule. A new publication arrives as a pending version; members see nothing new until you approve it. LTMS_REFERENCE_REFRESH_HOURS=0 turns it off.";
export const INTEGRATION_EMPTY = "This integration dataset has no items, so members can't find anything through it. Add item locators to a version, or connect members to the integration directly.";
export const INTEGRATION_CREDENTIAL = "The NAME of an environment variable on each member, like HF_TOKEN. Never paste the secret itself: LTMS doesn't store credentials.";
export const URL_SNAPSHOT = "LTMS fetches the page or file once and keeps it as a new version, never a live link, so lessons that cite it stay checkable. Only https hosts on LTMS_DATASET_URL_ALLOW; off when that's empty.";

// Fleet charter (#256)
export const CHARTER = "The charter is the fleet's governance: every member reads it when it joins and whenever it changes. Reads are logged and sent to the LRS.";
// "{days}" is replaced with the server's grace period.
export const CHARTER_GRACE = "Members must read this version. After {days} days, members who haven't read it can't write records until they do.";

// #295: a pending member that wants to be a hub.
export const HUB_INTENT =
  "Relay rights come from the swarm's root key: after approving, add it to the swarm and appoint it as hub.";

// #283: checking a pending member's key without exposing its secret.
export const FINGERPRINT_CHECK =
  "On the device, run python -m cli_agent pubkey --key <key file> and check that its public key starts with " +
  "these 16 characters. Don't open the key file itself: it contains the private key.";

// #284: the root key string, and what a bad paste does.
export const ROOT_KEY_FORMAT =
  "The whole line is the key. ltms-swarm-key-v1 is a label saying what kind of key it is and isn't secret; " +
  "the middle part is the secret; the last 8 characters are a checksum that catches copy mistakes. " +
  "Save and paste the entire line, label included.";
export const ROOT_KEY_PASTE =
  "A mistyped or incomplete key is rejected here, before anything is signed or sent. Nothing changes; paste it again.";
export const HEALTH_ABOUT = "The hub reading its own machine: temperatures, load, memory, power mode and over-current counters, as min/max/last over each window. A self-report, signed by the hub; not counted as sensing.";

// What each count means (#348), so no number looks made up.
export const COUNT_SENSE = "Sensed records (a sensor reading the world: camera, IMU, microphone) plus perceptions drawn from them. Each is signed by the member that made it. Heartbeats and health reports are not counted here.";
export const COUNT_ACT = "Thoughts, decisions, actions and events this member signed. The note shows how many actions reported success.";
export const COUNT_COMM = "Messages this member sent or received, including swarm messages relayed by a hub.";
export const COUNT_LEARN = "Lessons and policies: what this member learned from outcomes, and the rules it holds.";
export const COUNT_SELF_REPORT = "The member reporting on itself: heartbeat, uptime, members seen, temperature, memory. Not a perception of the world, so it is counted apart from Sensing.";
export const COUNT_HEARTBEAT = "A hub sends a heartbeat every few minutes (HUB_HEARTBEAT_SECONDS) so LTMS knows it is alive: uptime, members seen, dropped messages. It's a self-report.";
export const MSG_SUMMARY = "LTMS keeps every swarm message. Only the LRS copy of a busy member's messages (over 60 an hour, for 3 hours) is grouped into one statement per 5 minutes.";
export const MSG_OVER_HALF = "Messages are over half of this swarm's LRS traffic. Members over 60 messages/hour are already summarised; consider lowering the threshold in Settings.";

export const PING_ABOUT = "Asks the member for a heartbeat now. Its hub (or the member itself, if it talks to LTMS directly) checks for pings every 30 s; the answer is the next record LTMS receives from it. Heartbeats otherwise come twice a day.";
export const LEADER_ABOUT = "The leader directs the swarm. Members follow its directives by default and record any refusal with a reason. The fleet owner's directives and interventions still come first.";

export const SPECS_ABOUT = "What the device reports about itself (hardware, models, software), with any field an admin corrected. Each value says which. Not a record: not signed or sent to the LRS.";
