# Running a public demo

Set `ET_DEMO=on` to run Ember & Tide as a disposable public sandbox. Visitors open the app directly with no sign-in, try the bed controls and rhythm editor against the simulated bed, and see a persistent banner noting that changes reset every hour.

> **Never point demo mode at a real bed or a real household data folder.** `ET_DEMO=on` forces `ET_BED=sim`, clears the database on startup and every hour, and re-seeds the demo household and past nights.

## Quick start with Docker Compose

From the repository root:

```sh
docker compose -f docker-compose.demo.yml up -d
```

Open `http://localhost:8790`.

The example compose file runs the container with:

- `ET_DEMO=on` on port `8790`
- a named Docker volume (`demo-data`) for `/data`
- a read-only root filesystem with `/tmp` mounted as `tmpfs`

Put a TLS reverse proxy with per-IP rate limiting in front if you expose the demo on the open internet.

## What demo mode does

- **Simulated household and history:** forces `ET_BED=sim` even if another driver is set in environment variables, seeds two sample sleepers (`Alex` and `Sam`), a pet (`Milo`), and 7 past nights of telemetry and sleep stages, and resets all database and simulator state back to that baseline every hour.
- **Zero outbound network:** disables the push relay, Home Assistant MQTT bridge, external AI endpoints, cloud wearable polling, ICS calendars, ntfy, web push, and FCM. Send helpers assert demo mode is off before making any HTTP call.
- **No sign-in, visitor rate limit:** serves `/api/auth/state` as a read-safe visitor (`demo: true`, no password setup or login screen) and limits visitor mutations politely per client IP.

## What is allowed and what is blocked

Visitors can try interactive bed and schedule controls on the simulated bed:

- Reading tonight, sleep history, rhythms, insights, and Studio views
- `+ Warmer` / `- Cooler`, `Flash`, `I'm up`, `Guest`, `Away`, and sleep feedback
- Editing rhythms, person settings (such as units and half-degree Fahrenheit steps), pets, and bed orientation
- Built-in local assistant queries (without an external AI provider)

Anything that reaches outside the container, touches accounts or authentication, exports/imports data, or injects raw bed/simulator commands returns HTTP `403` (`Not available in the demo.`):

- Notification providers, push targets, web push subscriptions, and test sends (`/api/notify/*`)
- AI provider settings, model lists, and connection tests (`/api/ai/settings*`)
- Home Assistant connection settings and source tests (`/api/ha/*`, `/api/sources*`)
- External webhooks, polls, cloud wearable accounts, dial beacons, and MCP keys/endpoint (`/api/integrations/*`, `/api/hooks/*`, `/api/cloud/*`, `/api/oura/*`, `/mcp`)
- Sign-in, users, invites, tokens, and password changes (`/api/auth/*` except `/api/auth/state`)
- Data export, wearable/stage imports, hub/care configuration, raw bed console, and simulator clock/fault triggers (`/api/export`, `/api/console`, `/api/sim`, `/api/hub/*`, `/api/care/*`)
