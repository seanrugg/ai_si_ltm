import { describe, expect, it } from "vitest";
import * as ed from "@noble/ed25519";
import vectors from "./swarm-key-vectors.json";
import contractVectors from "../../../ltms_contract/vectors.json";
import {
  appointmentMessage,
  bytesToHex,
  canonicalize,
  decodeKey,
  encodeKey,
  generateRootKey,
  hexToBytes,
  KEY_PREFIX,
  nowTs,
  publicKeyHex,
  signAppointment,
  appointLeader,
  SwarmKeyError,
  swarmIdFor,
} from "./swarmKey.js";

describe("vectors shared with Python", () => {
  it("uses the same key prefix", () => {
    expect(KEY_PREFIX).toBe(vectors.key_prefix);
  });

  it.each(vectors.keys)("seed $seed_hex → key string, public key, swarm id", (v) => {
    const seed = hexToBytes(v.seed_hex);
    expect(encodeKey(seed)).toBe(v.key_string);
    expect(bytesToHex(decodeKey(v.key_string))).toBe(v.seed_hex);
    expect(publicKeyHex(seed)).toBe(v.public_key);
    expect(swarmIdFor(v.public_key)).toBe(v.swarm_id);
  });

  it.each(vectors.appointments)("appointment for $statement.hub: canonical bytes and signature", (v) => {
    expect(canonicalize(v.statement)).toBe(v.canonical);
    expect(bytesToHex(appointmentMessage(v.statement))).toBe(v.message_hex);
    const root = hexToBytes(v.root_seed_hex);
    const swarm = { id: v.statement.swarm, name: "echo-base" };
    const signed = signAppointment(encodeKey(root), {
      swarm, hub: v.statement.hub, hubKey: v.statement.hub_key, ts: v.statement.ts,
    });
    expect(signed.statement).toEqual(v.statement);
    expect(signed.sig).toBe(v.sig);
  });

  it.each(vectors.leader_appointments)("leader appointment for $statement.leader (#309)", (v) => {
    expect(canonicalize(v.statement)).toBe(v.canonical);
    expect(bytesToHex(appointmentMessage(v.statement))).toBe(v.message_hex);
    const signed = appointLeader(encodeKey(hexToBytes(v.root_seed_hex)), {
      swarm: { id: v.statement.swarm, name: "echo-base" }, leader: v.statement.leader,
      leaderKey: v.statement.leader_key, ts: v.statement.ts,
    });
    expect(signed).toEqual({ statement: v.statement, sig: v.sig });
  });

  it("canonicalizes the contract's JCS cases exactly like ltms_contract", () => {
    for (const c of contractVectors.canonical) expect(canonicalize(c.input)).toBe(c.output);
  });
});

describe("root keys", () => {
  it("a generated key round-trips through encode and decode", () => {
    const k = generateRootKey();
    expect(k.keyString.startsWith(`${KEY_PREFIX}:`)).toBe(true);
    expect(bytesToHex(decodeKey(k.keyString))).toBe(bytesToHex(k.seed));
    expect(k.swarmId).toBe(swarmIdFor(k.publicKey));
    expect(generateRootKey().keyString).not.toBe(k.keyString);
  });

  it("rejects a key with a bad checksum", () => {
    const good = vectors.keys[0].key_string;
    const bad = good.slice(0, -1) + (good.endsWith("0") ? "1" : "0");
    expect(() => decodeKey(bad)).toThrow(/checksum/);
  });

  it("rejects truncated keys and other text", () => {
    const good = vectors.keys[0].key_string;
    const [prefix, b64, sum] = good.split(":");
    expect(() => decodeKey(`${prefix}:${b64.slice(0, 20)}:${sum}`)).toThrow(SwarmKeyError);
    expect(() => decodeKey("hello")).toThrow(/not a swarm root key/);
    expect(() => decodeKey("")).toThrow(SwarmKeyError);
  });

  it("accepts the CLI's JSON key file", () => {
    const v = vectors.keys[1];
    const file = JSON.stringify({ name: "echo-base", secret_key: v.seed_hex, public_key: v.public_key });
    expect(bytesToHex(decodeKey(file))).toBe(v.seed_hex);
    const mismatched = JSON.stringify({ secret_key: v.seed_hex, public_key: vectors.keys[0].public_key });
    expect(() => decodeKey(mismatched)).toThrow(/doesn't match/);
  });
});

describe("signing appointments", () => {
  const v = vectors.appointments[0];

  it("refuses a key for another swarm before signing", () => {
    const otherKey = vectors.keys[1].key_string;
    expect(() => signAppointment(otherKey, {
      swarm: { id: v.statement.swarm, name: "echo-base" }, hub: "jetson-hub", hubKey: v.statement.hub_key,
    })).toThrow(/doesn't belong to swarm echo-base/);
  });

  it("produces a signature the swarm's public key verifies", () => {
    const signed = signAppointment(vectors.keys[0].key_string, {
      swarm: { id: vectors.keys[0].swarm_id, name: "echo-base" }, hub: "jetson-hub", hubKey: v.statement.hub_key,
    });
    const ok = ed.verify(hexToBytes(signed.sig), appointmentMessage(signed.statement),
      hexToBytes(vectors.keys[0].public_key));
    expect(ok).toBe(true);
  });

  it("formats ts like ltms_contract.now_ts", () => {
    expect(nowTs(new Date(Date.UTC(2026, 10, 8, 12, 0, 0, 5)))).toBe("2026-11-08T12:00:00.005Z");
  });
});
