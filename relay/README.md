# Ember & Tide push relay

A small Cloudflare Worker that forwards phone pushes to Firebase Cloud Messaging for servers that have no Firebase key
of their own. It holds one Firebase service-account key; servers register once and sign every request with a key of
their own.

Pushes are end-to-end encrypted by each server for the one phone they are for before they reach the relay, so the
relay only forwards ciphertext. It never logs request bodies, tokens or payloads (counts and error codes only) and
stores a hash of the registering address, not the address.

## Endpoints (JSON)

| | |
|---|---|
| `GET /v1/health` | `{ok, version}` |
| `POST /v1/register` | `{}` returns `{install_id, install_key}`. Limited to 5 per address per day. |
| `POST /v1/send` | Body `{install_id, ts, tokens[1..10], data: {e}, urgent, ios}`, header `X-Signature` = hex HMAC-SHA256 of the raw body with the install key. Returns `{results: [{token_suffix, status: ok\|gone\|error}]}`. |

`/v1/send` refuses a wrong signature or unknown install (401), a timestamp more than 5 minutes off (400), a body over 8 KB
(413), more than 10 tokens (400) and more than 500 messages a day per install (429). `gone` means Firebase says the
phone's registration no longer exists, so the server drops that device.

The Firebase message is built by `buildMessage` in `src/index.js`, which must match `build_message` in
`core/embertide/fcm.py`; both test suites check the shared `test/fixtures/message.json`.

## Deploy (maintainer)

1. `npx wrangler login`
2. `npx wrangler kv namespace create RELAY_KV`, and paste the id it prints into `wrangler.toml`.
3. `npx wrangler secret put FCM_SERVICE_ACCOUNT` and paste the whole Firebase service-account JSON (the project the
   app's `google-services.json` belongs to).
4. Optional: `npx wrangler secret put IP_SALT` with any random text, used when hashing addresses.
5. `npx wrangler deploy` (it also sets up the custom domain from `routes` in `wrangler.toml`).

Check it: `curl https://embertide.homepush.dev/v1/health`.

Hosting your own relay: change `routes` to your own domain (or remove it to use the workers.dev address) and set
`ET_PUSH_RELAY_URL` on your server to that address.

## Rotating

- **Firebase key:** create a new key in the Firebase console, run `wrangler secret put FCM_SERVICE_ACCOUNT` again, then
  delete the old key. No server changes needed.
- **One install:** delete its `i:<install_id>` entry in the KV namespace; that server re-registers by itself on its
  next push.

## Limits and notes

- Daily counters live in KV with a 2-day expiry. KV is not atomic, so limits are fair-use, not exact.
- The Workers free plan allows 100,000 requests a day and limited KV writes; each push is one request and one KV write.
- Tests: `node --test "relay/test/*.test.js"` (no dependencies; Node 20 or newer).
