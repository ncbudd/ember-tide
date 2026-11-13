# Configuration reference

The server is configured with environment variables (`core/embertide/config.py`, plus two read elsewhere). Everything has a
safe default: with nothing set you get the simulator on port 8790 with sign-in on. Empty values count as unset. Most
day-to-day settings (rhythms, notifications, devices, people) are in the app and Studio, not here.

## Environment variables

| Variable | Default | What it does |
|---|---|---|
| `ET_BED` | `sim` | Which bed driver to use: `sim` (built-in simulator), `orion-mqtt` (the Orion topper over local MQTT) or `orion-cloud` (the maker's cloud API), or `python:package.module:ClassName` for your own driver (see [drivers.md](drivers.md)). |
| `ET_TZ` | `TZ`, else the machine's zone, else `UTC` | Your IANA time zone, for example `Europe/London` or `Asia/Tokyo`. Schedules and nights are local time, so set this. The standard `TZ` variable is used when `ET_TZ` is empty. A name that is not a real zone is ignored. Studio, Your settings shows the zone in use and where it came from. |
| `ET_MQTT_URL` | `mqtt://localhost:1883` | The broker the bed talks to (`orion-mqtt` only). A login can go in the URL: `mqtt://user:password@host:1883`. |
| `ET_ORION_SERIAL` | auto | Serial number of the bed to control. Auto-detected from the first bed that reports; set it if several beds share the broker. |
| `ET_ORION_API_KEY` | none | Personal API key from the maker's web app (`orion-cloud` only). Never put it in a file you share. |
| `ET_DATA_DIR` | `./data` (the Docker image sets `/data`) | Where the database (`embertide.db`), the Web Push key, night traces and optional credentials live. Back this folder up. |
| `ET_HOST` | `0.0.0.0` | Address the server listens on. Use `127.0.0.1` if a reverse proxy on the same machine is the only client. |
| `ET_PORT` | `8790` | Port the server listens on (the Docker image sets 8790). |
| `ET_WEB_DIR` | `<repo>/web/dist` (the Docker image sets `/app/web`) | Folder with the built web app. |
| `ET_AUTH` | `on` | Sign-in. `off`, `0`, `false` or `no` turns it off: for testing on a trusted machine only, never on a reachable address. |
| `ET_PUBLIC_URL` | none | How phones reach the server, for example `https://sleep.example.com`. Makes buttons in notifications work. Can also be set in Studio, Notifications. |
| `ET_TRUST_PROXY` | `off` | `on` (or `1`, `true`, `yes`) behind a reverse proxy or tunnel, so rate limits see real visitor addresses. Forwarding headers (`X-Forwarded-For`, `Forwarded`, `X-Real-IP`) are believed only from a trusted proxy peer. |
| `ET_PROXY_CLOUDFLARE` | `off` | `on` only when traffic comes through Cloudflare (Tunnel or CDN). Believes `CF-Connecting-IP` from a trusted proxy peer; leave `off` behind non-Cloudflare proxies so visitors cannot spoof it. |
| `ET_TRUSTED_PROXIES` | none | Comma-separated addresses or CIDR ranges of your reverse proxy / tunnel, for example `172.18.0.0/16`. With `ET_TRUST_PROXY=on`, forwarding headers are believed only from these (empty falls back to any private or loopback peer). **Required** whenever `ET_AUTH_HEADER` or `ET_AUTH_HEADER_EMAIL` is set. |
| `ET_AUTH_HEADER` | none | Forward-auth: the header your sign-in proxy sets to the username (`Remote-User`, `X-Authentik-Username`, ...). Needs `ET_TRUST_PROXY=on` **and** `ET_TRUSTED_PROXIES` set. Matches an existing account only; see docs/install.md. |
| `ET_AUTH_HEADER_EMAIL` | none | Same, for a header carrying an email address (`Remote-Email`, `Cf-Access-Authenticated-User-Email`); matched against usernames. Needs `ET_TRUST_PROXY=on` and `ET_TRUSTED_PROXIES`. |
| `ET_HA_MQTT_URL` | none | Home Assistant's MQTT broker, for example `mqtt://user:password@192.168.1.10:1883`. Can also be set in Studio, Integrations. |
| `ET_MQTT_CA_FILE` | none | Path (inside the container) to the CA certificate that signed your bed broker's certificate, when `ET_MQTT_URL` is `mqtts://` (TLS, default port 8883). Empty = the system's trusted CAs. Note: the Orion hub itself only speaks plain MQTT on 1883, so the usual bed broker stays `mqtt://`; use TLS only if you put your own TLS broker or bridge in between. |
| `ET_MQTT_TLS_INSECURE` | `off` | `on` skips certificate checks for an `mqtts://` bed broker (self-signed, trusted network only). |
| `ET_HA_MQTT_CA_FILE` | none | Same as `ET_MQTT_CA_FILE`, for the Home Assistant bridge (`ET_HA_MQTT_URL=mqtts://...`, port 8883 by default). |
| `ET_HA_MQTT_TLS_INSECURE` | `off` | `on` skips certificate checks for an `mqtts://` Home Assistant broker (self-signed). |
| `ET_HA_DISCOVERY_PREFIX` | `homeassistant` | MQTT discovery prefix used for Home Assistant entities. Change only if you changed it in Home Assistant. |
| `ET_FCM_CREDENTIALS` | `<data dir>/fcm-service-account.json` | Path to your own Firebase service-account key, to send Google push notifications to the Android app. Optional. With this key present, pushes go straight to Google and never touch the relay below. |
| `ET_PUSH_RELAY` | `on` | Phone pushes for the Android app use the Ember & Tide push relay when this server has no Firebase key of its own. `off`, `0` or `false` turns the relay off (Web Push, ntfy, Home Assistant and webhooks are unaffected). See [notifications.md](notifications.md#the-push-relay). |
| `ET_PUSH_RELAY_URL` | `https://embertide.homepush.dev` | Address of the relay. Change it only to use a relay you host yourself (`relay/`). |
| `ET_LLM_URL` | none | Default for the AI model (the choice saved in Studio, AI model, overrides all four `ET_LLM_*` settings; see [ai-models.md](ai-models.md)). An OpenAI-compatible endpoint for the optional "Ask" assistant and morning review, for example Ollama at `http://host:11434/v1`. Unset means built-in analysis only; nothing leaves the house. |
| `ET_LLM_MODEL` | `qwen2.5:7b` | Model name sent to that endpoint. |
| `ET_LLM_KEY` | none | API key for the endpoint, if it needs one. |
| `ET_LLM_PROVIDER` | `openai` | `openai` (any OpenAI-compatible server) or `anthropic` (needs `pip install "embertide[claude]"`; key from `ET_LLM_KEY` or the SDK's own credentials). |
| `ET_SIM_SPEED` | `1` | Clock speed for the simulator only (`ET_BED=sim`). For example `60` runs a minute per second. Also changeable in Studio, Simulator. |
| `ET_TRACE` | `on` | A compact JSON line every 15 seconds in `<data dir>/traces/`, kept 14 days, for tuning presence on real nights (no names). `off`, `0` or `false` disables it. |
| `TZ` | none | Standard time-zone variable; used by the container and as the fallback for `ET_TZ`. |

That is 32 variables (31 `ET_` plus `TZ`). Settings that are secrets (`ET_ORION_API_KEY`, `ET_LLM_KEY`, URLs with passwords) are best
kept in a `.env` file next to your Compose file with restricted permissions, which is git-ignored here (`.env.example` lists every variable to copy from):

```yaml
    env_file: .env
```

## Files in the data folder

| Path | Created by | Notes |
|---|---|---|
| `embertide.db` (+ `-wal`, `-shm`) | the server | SQLite database: everything. |
| `vapid-private.pem` | first Web Push use | The server's Web Push key. Keep it private. |
| `fcm-service-account.json` | you | Optional Google push credentials. |
| `traces/trace-YYYY-MM-DD.jsonl` | the server | Presence tuning traces, 14 days. |

## Ports

| Port | What | Exposure |
|---|---|---|
| 8790 | web app, Studio and API | LAN, or behind HTTPS if reachable from outside |
| 1883 | the bed's MQTT broker (the Compose example) | LAN only, never forwarded |

## Settings kept in the app

Rhythms, people and their preferences, Autopilot, devices (radar, scales, room sensors, calendar, bedside dials), notification
targets, Home Assistant, cloud wearable accounts and invitations are stored in the database and edited in the app and Studio. See
[settings-map.md](settings-map.md) for where each one lives and [integrations.md](integrations.md) for cloud accounts and generic
webhooks.

## Units, clock, language and seasons

These are per person or per household settings in the app (Settings, or Studio, Your settings), not environment variables:

- **Temperature** `°F` or `°C`. A new person starts on the unit of their browser's region (°F for the US and its territories,
  Liberia and Myanmar; °C elsewhere). Weights follow it unless picked.
- **Clock** Auto, 12 h or 24 h. Auto means 24 h with °C and 12 h with °F in messages the server writes. The first-time setup
  saves the device's own style.
- **Assistant language** for Ask, the assistant and night reviews. Automatic: the language you write in (English for reviews).
  Screens, buttons and notification wording are English for now.
- **Seasons** (household): the AI review mentions the time of year ("winter", "spring"). Automatic works it out from the time
  zone (southern hemisphere zones flip the seasons; zones near the equator get no season word). Override in Studio, Your settings.
