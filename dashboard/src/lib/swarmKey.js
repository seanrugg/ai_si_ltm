// Swarm root keys in the browser (issue #122).
//
// The root key is generated here, shown once, and never sent to the server or
// written to any browser storage. Signing a hub appointment also happens here;
// only {statement, sig} goes to the server. Byte compatibility with Python
// (scripts/swarm_key.py, server/services/swarms.py) is pinned by
// swarm-key-vectors.json, which both test suites check.
import * as ed from "@noble/ed25519";
import { sha256, sha512 } from "@noble/hashes/sha2.js";

ed.hashes.sha512 = sha512; // enables ed25519's synchronous API (see its README)

export const KEY_PREFIX = "ltms-swarm-key-v1";
const SWARM_PREFIX = new TextEncoder().encode("ltms-swarm-v1\n"); // must match server/services/swarms.py

export class SwarmKeyError extends Error {}

// ------------------------------------------------------------------ encoding
export function bytesToHex(bytes) {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function hexToBytes(hex) {
  if (typeof hex !== "string" || hex.length % 2 || /[^0-9a-f]/i.test(hex)) {
    throw new SwarmKeyError("not a hex string");
  }
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}

function toBase64Url(bytes) {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromBase64Url(text) {
  if (/[^A-Za-z0-9_-]/.test(text)) throw new SwarmKeyError("the key has characters that don't belong in it");
  const b64 = text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (text.length % 4)) % 4);
  let bin;
  try {
    bin = atob(b64);
  } catch {
    throw new SwarmKeyError("the key isn't valid base64url");
  }
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

function checksum(seed) {
  return bytesToHex(sha256(seed)).slice(0, 8);
}

// JCS (RFC 8785): sorted keys (UTF-16 code unit order, which is JS's default
// sort), no whitespace, ES number and string serialization. Same bytes as
// ltms_contract.canonicalize for JSON values.
export function canonicalize(value) {
  if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new SwarmKeyError("JCS can't encode NaN or Infinity");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalize).join(",")}]`;
  if (typeof value === "object") {
    const keys = Object.keys(value).sort();
    return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalize(value[k])}`).join(",")}}`;
  }
  throw new SwarmKeyError(`JCS can't encode a ${typeof value}`);
}

// ------------------------------------------------------------------ keys
/** The key string: ltms-swarm-key-v1:<base64url seed>:<first 8 hex of sha256(seed)>. */
export function encodeKey(seed) {
  if (!(seed instanceof Uint8Array) || seed.length !== 32) throw new SwarmKeyError("a root key seed is 32 bytes");
  return `${KEY_PREFIX}:${toBase64Url(seed)}:${checksum(seed)}`;
}

/**
 * Read a pasted root key and return its 32-byte seed. Accepts the key string
 * the dashboard shows, or the JSON key file `scripts/swarm_key.py create`
 * writes. Throws SwarmKeyError with a readable message otherwise.
 */
export function decodeKey(text) {
  const input = (text || "").trim();
  if (input.startsWith("{")) return decodeKeyFile(input);
  const parts = input.split(":");
  if (parts.length !== 3 || parts[0] !== KEY_PREFIX) {
    throw new SwarmKeyError(`not a swarm root key (it should start with "${KEY_PREFIX}:")`);
  }
  const seed = fromBase64Url(parts[1]);
  if (seed.length !== 32) throw new SwarmKeyError("the key is the wrong length; check it was copied in full");
  if (parts[2].toLowerCase() !== checksum(seed)) {
    throw new SwarmKeyError("the key's checksum doesn't match; check it was copied exactly");
  }
  return seed;
}

function decodeKeyFile(text) {
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    throw new SwarmKeyError("that looks like a key file, but it isn't valid JSON");
  }
  if (typeof data?.secret_key !== "string") throw new SwarmKeyError("the key file has no secret_key");
  const seed = hexToBytes(data.secret_key);
  if (seed.length !== 32) throw new SwarmKeyError("the key file's secret_key is the wrong length");
  if (data.public_key && data.public_key.toLowerCase() !== publicKeyHex(seed)) {
    throw new SwarmKeyError("the key file's public_key doesn't match its secret_key");
  }
  return seed;
}

export function publicKeyHex(seed) {
  return bytesToHex(ed.getPublicKey(seed));
}

/** The swarm ID is sha256(public key), hex, as in server/services/swarms.swarm_id_for. */
export function swarmIdFor(publicKeyHexValue) {
  return bytesToHex(sha256(hexToBytes(publicKeyHexValue)));
}

/** A fresh root key from crypto.getRandomValues. */
export function generateRootKey() {
  const seed = crypto.getRandomValues(new Uint8Array(32));
  const pub = publicKeyHex(seed);
  return { seed, keyString: encodeKey(seed), publicKey: pub, swarmId: swarmIdFor(pub) };
}

// ------------------------------------------------------------------ appointments
/** Same format as ltms_contract.now_ts: UTC, milliseconds, Z. */
export function nowTs(date = new Date()) {
  return date.toISOString();
}

export function appointmentStatement({ swarmId, hub, hubKey, ts }) {
  return { v: 1, action: "appoint_hub", swarm: swarmId, hub, hub_key: hubKey.toLowerCase(), ts };
}

export function appointmentMessage(statement) {
  const body = new TextEncoder().encode(canonicalize(statement));
  const out = new Uint8Array(SWARM_PREFIX.length + body.length);
  out.set(SWARM_PREFIX, 0);
  out.set(body, SWARM_PREFIX.length);
  return out;
}

/**
 * Sign a hub appointment with a pasted root key. Refuses, before signing, a
 * key that doesn't belong to the swarm (its sha256(public key) isn't swarm.id).
 * Returns {statement, sig}, the body for POST /api/admin/swarms/{name}/hub.
 */
export function signAppointment(keyText, { swarm, hub, hubKey, ts = nowTs() }) {
  const seed = decodeKey(keyText);
  try {
    if (swarmIdFor(publicKeyHex(seed)) !== swarm.id) {
      throw new SwarmKeyError(`this key doesn't belong to swarm ${swarm.name}`);
    }
    if (!/^[0-9a-f]{64}$/i.test(hubKey || "")) throw new SwarmKeyError("the hub's public key is missing");
    const statement = appointmentStatement({ swarmId: swarm.id, hub, hubKey, ts });
    const sig = bytesToHex(ed.sign(appointmentMessage(statement), seed));
    return { statement, sig };
  } finally {
    seed.fill(0);
  }
}

/** A swarm leader appointment (#309): the hub's framing, action ``appoint_leader``. */
export function leaderStatement({ swarmId, leader, leaderKey, ts }) {
  return { v: 1, action: "appoint_leader", swarm: swarmId, leader, leader_key: leaderKey.toLowerCase(), ts };
}

/** Sign a leader appointment with the root key in the browser; the seed is wiped after. */
export function appointLeader(keyText, { swarm, leader, leaderKey, ts = nowTs() }) {
  const seed = decodeKey(keyText);
  try {
    if (swarmIdFor(publicKeyHex(seed)) !== swarm.id) {
      throw new SwarmKeyError(`this key doesn't belong to swarm ${swarm.name}`);
    }
    if (!/^[0-9a-f]{64}$/i.test(leaderKey || "")) throw new SwarmKeyError("the leader's public key is missing");
    const statement = leaderStatement({ swarmId: swarm.id, leader, leaderKey, ts });
    const sig = bytesToHex(ed.sign(appointmentMessage(statement), seed));
    return { statement, sig };
  } finally {
    seed.fill(0);
  }
}
