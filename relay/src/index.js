// Ember & Tide push relay: a Cloudflare Worker that forwards already-encrypted phone pushes to Firebase.
//
// Servers without their own Firebase key sign a request with a per-install key; we check it, then send each token an
// FCM message. The payload is end-to-end encrypted by the server for each phone, so this code only ever handles
// ciphertext. Nothing here logs request bodies, tokens or payloads: only counts and error codes.

export const VERSION = "1.0.0";
const MAX_BODY = 8 * 1024;
const MAX_TOKENS = 10;
const MAX_SKEW = 5 * 60;            // seconds either side of now
const REGISTER_PER_DAY = 5;         // per client address
const SEND_PER_DAY = 500;           // per install
const TOKEN_RE = /^[A-Za-z0-9_:-]{100,400}$/;
const B64U_RE = /^[A-Za-z0-9_-]{1,6000}$/;
const SCOPE = "https://www.googleapis.com/auth/firebase.messaging";

const enc = new TextEncoder();
const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });
const b64u = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const hex = (bytes) => [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");
const unhex = (s) => (/^([0-9a-f]{2})+$/i.test(s) ? Uint8Array.from(s.match(/../g), (h) => parseInt(h, 16)) : null);
const randomB64u = (n) => b64u(crypto.getRandomValues(new Uint8Array(n)));
const day = (now) => Math.floor(now / 86400);

// The FCM v1 message. Must stay identical to build_message() in core/embertide/fcm.py (a shared fixture checks both).
export function buildMessage(token, e, urgent, ios, nowSec) {
  const message = { token, data: { e }, android: { priority: urgent ? "high" : "normal", ttl: "21600s" } };
  if (ios) {
    message.apns = {
      headers: { "apns-priority": urgent ? "10" : "5", "apns-push-type": "alert", "apns-expiration": String(Math.floor(nowSec) + 21600) },
      payload: { aps: { alert: { title: "Ember & Tide", body: "New message" }, "mutable-content": 1, sound: "default" } },
    };
  }
  return { message };
}

// Constant-time check of the hex HMAC-SHA256 of the raw body (subtle.verify compares in constant time).
export async function signatureOk(keyB64u, rawBody, sigHex) {
  const sig = unhex(sigHex || "");
  if (!sig || sig.length !== 32) return false;
  const key = await crypto.subtle.importKey("raw", enc.encode(keyB64u), { name: "HMAC", hash: "SHA-256" }, false, ["verify"]);
  return crypto.subtle.verify("HMAC", key, sig, rawBody);
}

export function timestampOk(ts, nowSec) {
  return Number.isFinite(ts) && Math.abs(nowSec - ts) <= MAX_SKEW;
}

// A KV counter with a 2-day expiry (KV is not atomic: this is a fair-use limit, not a hard guarantee).
async function bump(kv, key, limit) {
  const n = parseInt((await kv.get(key)) || "0", 10);
  if (n >= limit) return false;
  await kv.put(key, String(n + 1), { expirationTtl: 2 * 86400 });
  return true;
}

async function hashIp(ip, env) {
  return hex(await crypto.subtle.digest("SHA-256", enc.encode(`${env.IP_SALT || "ember-tide"}|${ip}`))).slice(0, 32);
}

async function register(request, env, now) {
  const ipHash = await hashIp(request.headers.get("CF-Connecting-IP") || "unknown", env);
  if (!(await bump(env.RELAY_KV, `r:${ipHash}:${day(now)}`, REGISTER_PER_DAY))) return json({ error: "too many registrations today" }, 429);
  const install_id = randomB64u(16);
  const install_key = randomB64u(32);
  await env.RELAY_KV.put(`i:${install_id}`, JSON.stringify({ key: install_key, created: now, ip_hash: ipHash }));
  return json({ install_id, install_key });
}

// ---- Firebase access token: sign a service-account JWT with RS256 (WebCrypto), cache it until it expires ----
let cached = null; // { token, exp, for }

function pemToDer(pem) {
  const body = pem.replace(/-----[A-Z ]+-----/g, "").replace(/\s+/g, "");
  return Uint8Array.from(atob(body), (c) => c.charCodeAt(0));
}

async function accessToken(sa, now) {
  if (cached && cached.for === sa.client_email && cached.exp > now + 60) return cached.token;
  const head = b64u(enc.encode(JSON.stringify({ alg: "RS256", typ: "JWT" })));
  const tokenUri = sa.token_uri || "https://oauth2.googleapis.com/token";
  const claim = b64u(enc.encode(JSON.stringify({ iss: sa.client_email, scope: SCOPE, aud: tokenUri, iat: now, exp: now + 3600 })));
  const key = await crypto.subtle.importKey("pkcs8", pemToDer(sa.private_key), { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, enc.encode(`${head}.${claim}`));
  const r = await fetch(tokenUri, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer", assertion: `${head}.${claim}.${b64u(sig)}` }),
  });
  if (!r.ok) throw new Error(`token ${r.status}`);
  const j = await r.json();
  cached = { token: j.access_token, exp: now + Number(j.expires_in || 3600), for: sa.client_email };
  return cached.token;
}

// Same decisions as Fcm.send in core/embertide/fcm.py: "gone" means the phone's registration will never work again.
export function classify(status, text) {
  if (status >= 200 && status < 300) return "ok";
  if (status === 404 || (status === 400 && text.includes("UNREGISTERED")) || text.includes("registration-token-not-registered")) return "gone";
  if ((status === 400 && text.includes("INVALID_ARGUMENT") && text.includes("registration token")) || (status === 403 && text.includes("SENDER_ID_MISMATCH"))) return "gone";
  return "error";
}

async function sendOne(sa, token, e, urgent, ios, now) {
  try {
    const at = await accessToken(sa, now);
    const r = await fetch(`https://fcm.googleapis.com/v1/projects/${sa.project_id}/messages:send`, {
      method: "POST",
      headers: { authorization: `Bearer ${at}`, "content-type": "application/json" },
      body: JSON.stringify(buildMessage(token, e, urgent, ios, now)),
    });
    if (r.status === 401) cached = null;
    return classify(r.status, r.ok ? "" : await r.text());
  } catch {
    return "error";
  }
}

async function send(request, env, now) {
  const raw = await request.arrayBuffer();
  if (raw.byteLength > MAX_BODY) return json({ error: "body too large" }, 413);
  let body;
  try { body = JSON.parse(new TextDecoder().decode(raw)); } catch { return json({ error: "bad request" }, 400); }
  if (!body || typeof body.install_id !== "string" || !/^[A-Za-z0-9_-]{10,40}$/.test(body.install_id)) return json({ error: "bad request" }, 400);
  // Same answer for "unknown install" and "wrong signature", so nobody can probe which ids exist.
  const rec = JSON.parse((await env.RELAY_KV.get(`i:${body.install_id}`)) || "null");
  if (!rec || !(await signatureOk(rec.key, raw, request.headers.get("X-Signature")))) return json({ error: "unauthorized" }, 401);
  if (!timestampOk(body.ts, now)) return json({ error: "clock is off or request is old" }, 400);
  const tokens = body.tokens;
  if (!Array.isArray(tokens) || tokens.length < 1 || tokens.length > MAX_TOKENS || !tokens.every((t) => typeof t === "string" && TOKEN_RE.test(t))) return json({ error: "1 to 10 push tokens needed" }, 400);
  const e = body.data && body.data.e;
  if (typeof e !== "string" || !B64U_RE.test(e)) return json({ error: "bad payload" }, 400);
  if (!(await bump(env.RELAY_KV, `s:${body.install_id}:${day(now)}`, SEND_PER_DAY))) return json({ error: "daily limit reached" }, 429);

  let sa;
  try { sa = JSON.parse(env.FCM_SERVICE_ACCOUNT); } catch { return json({ error: "relay isn't set up" }, 503); }
  const results = [];
  for (const t of tokens) results.push({ token_suffix: t.slice(-8), status: await sendOne(sa, t, e, !!body.urgent, !!body.ios, now) });
  console.log(`send: ${results.length} tokens, ${results.filter((x) => x.status === "ok").length} ok`);   // counts only
  return json({ results });
}

export async function handle(request, env, now = Math.floor(Date.now() / 1000)) {
  const { pathname } = new URL(request.url);
  if (pathname === "/v1/health" && request.method === "GET") return json({ ok: true, version: VERSION });
  if (request.method !== "POST") return json({ error: "not found" }, 404);
  if (pathname === "/v1/register") return register(request, env, now);
  if (pathname === "/v1/send") return send(request, env, now);
  return json({ error: "not found" }, 404);
}

export default { fetch: (request, env) => handle(request, env).catch(() => json({ error: "server error" }, 500)) };
