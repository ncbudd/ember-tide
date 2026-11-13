import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { buildMessage, classify, handle, signatureOk, timestampOk } from "../src/index.js";

const NOW = 1800000000;
const TOKEN = "t".repeat(150);
const kvStore = () => { const m = new Map(); return { m, get: async (k) => m.get(k) ?? null, put: async (k, v) => { m.set(k, v); } }; };
const hmacHex = async (key, body) => {
  const k = await crypto.subtle.importKey("raw", new TextEncoder().encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return [...new Uint8Array(await crypto.subtle.sign("HMAC", k, body))].map((b) => b.toString(16).padStart(2, "0")).join("");
};
async function serviceAccount() {
  const kp = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
  const der = Buffer.from(await crypto.subtle.exportKey("pkcs8", kp.privateKey)).toString("base64");
  return JSON.stringify({ type: "service_account", project_id: "demo", client_email: "p@demo.iam", private_key: `-----BEGIN PRIVATE KEY-----\n${der}\n-----END PRIVATE KEY-----\n` });
}
async function setup() {
  const env = { RELAY_KV: kvStore(), FCM_SERVICE_ACCOUNT: await serviceAccount() };
  const reg = await (await handle(new Request("https://r/v1/register", { method: "POST", headers: { "CF-Connecting-IP": "1.2.3.4" }, body: "{}" }), env, NOW)).json();
  return { env, reg };
}
async function post(env, reg, over = {}, opts = {}) {
  const raw = new TextEncoder().encode(JSON.stringify({ install_id: reg.install_id, ts: NOW, tokens: [TOKEN], data: { e: "c2VjcmV0" }, urgent: true, ios: false, ...over }));
  const sig = opts.sig ?? (await hmacHex(opts.key ?? reg.install_key, raw));
  return handle(new Request("https://r/v1/send", { method: "POST", headers: { "X-Signature": sig }, body: raw }), env, NOW);
}
const mockFcm = (status = 200, text = "{}") => {
  const seen = [];
  globalThis.fetch = async (url, init) => {
    seen.push({ url: String(url), init });
    if (String(url).includes("oauth2")) return new Response(JSON.stringify({ access_token: "ya29.x", expires_in: 3600 }));
    return new Response(text, { status });
  };
  return seen;
};

test("message builder matches the shared fixture (the Python side reads the same file)", () => {
  for (const c of JSON.parse(readFileSync(new URL("./fixtures/message.json", import.meta.url)))) {
    const i = c.input;
    assert.deepEqual(buildMessage(i.token, i.e, i.urgent, i.ios, i.now), c.expected, c.name);
  }
});

test("signature: right key passes; wrong key, tampered body, junk fail", async () => {
  const body = new TextEncoder().encode('{"a":1}');
  const sig = await hmacHex("k", body);
  assert.equal(await signatureOk("k", body, sig), true);
  assert.equal(await signatureOk("other", body, sig), false);
  assert.equal(await signatureOk("k", new TextEncoder().encode('{"a":2}'), sig), false);
  assert.equal(await signatureOk("k", body, "zz"), false);
  assert.equal(await signatureOk("k", body, null), false);
});

test("timestamp window is 5 minutes either side", () => {
  assert.ok(timestampOk(NOW - 299, NOW) && timestampOk(NOW + 300, NOW));
  assert.ok(!timestampOk(NOW - 301, NOW) && !timestampOk(NOW + 301, NOW) && !timestampOk("x", NOW));
});

test("register gives id and key, stores them, and limits to 5 per address per day", async () => {
  const env = { RELAY_KV: kvStore() };
  const go = () => handle(new Request("https://r/v1/register", { method: "POST", headers: { "CF-Connecting-IP": "9.9.9.9" }, body: "{}" }), env, NOW);
  const first = await (await go()).json();
  assert.ok(first.install_id && first.install_key);
  assert.ok(env.RELAY_KV.m.has(`i:${first.install_id}`));
  assert.ok(![...env.RELAY_KV.m.values()].some((v) => v.includes("9.9.9.9")));   // the address is only stored hashed
  for (let i = 0; i < 4; i++) assert.equal((await go()).status, 200);
  assert.equal((await go()).status, 429);
});

test("send: ok path builds the Firebase message and answers with token suffixes only", async () => {
  const { env, reg } = await setup();
  const seen = mockFcm();
  const r = await post(env, reg);
  assert.deepEqual(await r.json(), { results: [{ token_suffix: TOKEN.slice(-8), status: "ok" }] });
  const msg = JSON.parse(seen[1].init.body);
  assert.deepEqual(msg, buildMessage(TOKEN, "c2VjcmV0", true, false, NOW));
  assert.ok(seen[1].url.endsWith("/v1/projects/demo/messages:send") && seen[1].init.headers.authorization === "Bearer ya29.x");
});

test("send: wrong signature, unknown install, stale timestamp are refused", async () => {
  const { env, reg } = await setup();
  mockFcm();
  assert.equal((await post(env, reg, {}, { key: "wrong" })).status, 401);
  assert.equal((await post(env, { ...reg, install_id: "unknown-install-id" })).status, 401);
  assert.equal((await post(env, reg, { ts: NOW - 400 })).status, 400);
});

test("send: size and token limits", async () => {
  const { env, reg } = await setup();
  mockFcm();
  assert.equal((await post(env, reg, { tokens: Array(11).fill(TOKEN) })).status, 400);
  assert.equal((await post(env, reg, { tokens: [] })).status, 400);
  assert.equal((await post(env, reg, { tokens: ["short"] })).status, 400);
  assert.equal((await post(env, reg, { data: { e: "A".repeat(9000) } })).status, 413);
  assert.equal((await post(env, reg, { tokens: Array(10).fill(TOKEN) })).status, 200);
});

test("send: 500 messages a day per install", async () => {
  const { env, reg } = await setup();
  mockFcm();
  env.RELAY_KV.m.set(`s:${reg.install_id}:${Math.floor(NOW / 86400)}`, "500");
  assert.equal((await post(env, reg)).status, 429);
});

test("gone and error results", async () => {
  assert.equal(classify(404, "UNREGISTERED"), "gone");
  assert.equal(classify(400, "UNREGISTERED"), "gone");
  assert.equal(classify(400, "INVALID_ARGUMENT The registration token is not a valid FCM registration token"), "gone");
  assert.equal(classify(400, "INVALID_ARGUMENT bad payload"), "error");
  assert.equal(classify(403, "SENDER_ID_MISMATCH"), "gone");
  assert.equal(classify(500, "oops"), "error");
  const { env, reg } = await setup();
  mockFcm(404, '{"error":{"details":[{"errorCode":"UNREGISTERED"}]}}');
  assert.equal((await (await post(env, reg)).json()).results[0].status, "gone");
});

test("health", async () => {
  const j = await (await handle(new Request("https://r/v1/health"), {}, NOW)).json();
  assert.equal(j.ok, true);
});
