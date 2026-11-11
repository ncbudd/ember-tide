# Installing Ember & Tide

Pick the way that suits you. Everything runs as one small server on your home network; phones, tablets, the
watch tile and Home Assistant all talk to it.

| I want to... | Read |
|---|---|
| Try it without a bed, or run it for real, in Docker Compose | [1. Docker Compose](#1-docker-compose-recommended) |
| Use plain `docker run` | [2. Plain Docker](#2-plain-docker) |
| Work on the code | [3. From source](#3-run-from-source-for-development) |
| Portainer, Synology, QNAP, TrueNAS, home-server distributions | [4. NAS and container managers](#4-nas-and-container-managers) |
| Reach it away from home, or use iPhone notifications | [5. HTTPS: reverse proxy or tunnel](#5-https-reverse-proxy-or-tunnel) |
| Home Assistant | [6. Home Assistant](#6-home-assistant) |
| Connect the bed itself | [Bed cutover](bed-cutover.md) |
| Android app, widget, Wear OS tile | [Android](android.md) |
| iPhone | [7. iPhone](#7-iphone-home-screen-app) |
| First sign-in and setup | [8. First run](#8-first-run) |
| Upgrade, back up, restore | [9. Upgrading, backups, restoring](#9-upgrading-backups-restoring) |
| Every setting | [Configuration](configuration.md) |
| Something is wrong | [Troubleshooting](troubleshooting.md) |

Plan for about 15 minutes for the server, plus 10 for the bed. You can do the server first with the built-in
simulator and connect the bed later.

## What you need

- A machine that is on all the time at home: a mini PC, a NAS, a Raspberry Pi 4 or newer, or a spare laptop. It
  needs Docker (Compose v2) or Python 3.12.
- For the real bed: an Orion Sleep topper, a router or DNS server where you can add one local DNS record, and a
  fixed address (DHCP reservation) for the machine running the broker. See [Bed cutover](bed-cutover.md).
- Optional: Home Assistant, a bed strip or radar, scales, a watch or ring, the bedside dial. None of them is required.

---

## 1. Docker Compose (recommended)

```bash
git clone https://github.com/Ncbudd/ember-tide.git      # or unpack a release
cd ember-tide
cp docker-compose.example.yml docker-compose.yml
```

Edit `docker-compose.yml` and set your time zone in both `TZ` and `ET_TZ` (for example `Europe/London`).
Schedules are local time, so a wrong zone means a wrong bedtime. Then:

```bash
docker compose up -d --build
docker compose ps            # both services should say "healthy" after about a minute
```

Open `http://<machine-address>:8790` from a phone or computer **on your home network**. You will be asked to create the owner account
(see [First run](#8-first-run)). With the default `ET_BED: sim`, you are looking at a simulated bed: use
**Studio → Simulator** to jump to bedtime, speed up the clock and put people in or out of bed.

What the example file starts:

| Service | Image | Purpose |
|---|---|---|
| `embertide` | built from this folder (web app and server in one image) | the app, Studio and the API on port 8790; data in `./data` |
| `mosquitto` | `eclipse-mosquitto:2.0.20` | the MQTT broker the bed talks to, on port 1883, with an access list |

The broker configuration lives in `deploy/mosquitto/` (`mosquitto.conf` and `acl`, both commented). It allows
anonymous clients on port 1883, but only on the bed's two topics, `orion/<serial>/in` and `orion/<serial>/out`.
That is how the bed's local protocol works (the bed has no broker password), so keep the port on your LAN.

**Pin the broker version.** Use Mosquitto **2.0.20**. Version 2.0.21 changed keep-alive handling in a way the bed's
firmware does not like, and the bed disconnects now and then. The example file pins the tag; when you want to try a newer
one, change it, watch `docker compose logs mosquitto` for a day, and go back if the bed keeps reconnecting.

To use a real bed, edit the two lines in `docker-compose.yml` as the comments say (`ET_BED: orion-mqtt` and
`ET_MQTT_URL: mqtt://mosquitto:1883`), run `docker compose up -d`, and follow [Bed cutover](bed-cutover.md).

No bed at all but you have an Orion account? Use the cloud driver instead: `ET_BED: orion-cloud` and
`ET_ORION_API_KEY` (create the key in the Orion web app). It polls about every 10 seconds, needs the internet, and
leaves the official app working. You can then delete the `mosquitto` service.

Already have a broker you want to use? Delete the `mosquitto` service and the `depends_on` block, and set
`ET_MQTT_URL` to it (credentials can go in the URL: `mqtt://user:password@192.168.1.10:1883`). The bed still needs a
listener on port 1883 without a login, so the same advice about topics applies; copy the two `topic` lines from
`deploy/mosquitto/acl` into that broker's access list.

## 2. Plain Docker

```bash
docker build -t ember-tide .
docker run -d --name ember-tide --restart unless-stopped \
  -p 8790:8790 \
  -v ember-tide-data:/data \
  -e ET_TZ=America/New_York \
  ember-tide
```

That runs the simulator. For a real bed against a broker you already run, add
`-e ET_BED=orion-mqtt -e ET_MQTT_URL=mqtt://192.168.1.10:1883`. A named volume (above) or a folder
(`-v /srv/ember-tide:/data`) both work; the database is `/data/embertide.db`.

The image listens on `0.0.0.0:8790` (`ET_HOST`, `ET_PORT`), keeps data in `/data`, and has a health check on
`/healthz`.

## 3. Run from source (for development)

You need Python 3.12 and [uv](https://docs.astral.sh/uv/), plus Node 20 or newer for the web app.

```bash
# server and tests
cd core
uv venv
uv pip install -e ".[dev]"
uv run pytest -q

# web app (the server serves web/dist if it exists)
cd ../web
npm ci
npm run build

# run it: simulator, sign-in off, throwaway data folder
cd ../core
ET_AUTH=off ET_TZ=America/New_York ET_DATA_DIR=./data-dev uv run python -m embertide
```

Open `http://localhost:8790`. While working on the interface, run `npm run dev` in `web/` instead; it proxies the API to
port 8790 and reloads on save. `ET_AUTH=off` removes sign-in and is for local testing only. Set `ET_WEB_DIR` if you
keep the built web app somewhere else. On Windows, use `.venv\Scripts\python.exe -m embertide` and set the variables
with `$env:NAME = "value"`.

Type checks and builds: `cd web && npx tsc --noEmit -p . && npm run build`.

## 4. NAS and container managers

Any manager that can import a Compose file works; the file is self-contained apart from the `deploy/mosquitto`
folder and the build context.

- **Home-server distributions with a Docker tab and a Compose plugin**: install the Compose plugin (or use the Docker tab's "Add Container" with the plain-Docker
  settings above). Put the project under the system appdata share (for example `/mnt/user/appdata/ember-tide/`), with `./data` next to it so the
  database is on the array or a pool you back up. Give the broker container a fixed LAN address or use the host's;
  the bed needs to reach port 1883.
- **Portainer**: Stacks, Add stack, paste the Compose file (or point at the Git repository). Because the stack
  builds from source, use the "Repository" option so the build context and `deploy/mosquitto` are available, or build
  the image once on the host and replace `build: .` with `image: ember-tide`. Bind-mount the two Mosquitto files
  from a host folder.
- **Synology (Container Manager) and QNAP**: Project, Create, choose the folder that holds the Compose file and
  `deploy/`. Keep `./data` on a volume that is part of your backup.
- **TrueNAS and others**: use the plain-Docker command or the Compose import; the only requirements are persistent
  storage for `/data` and port 1883 reachable by the bed.

If port 1883 is already used on that machine by another broker, either point the bed's DNS record at a different
machine or give this broker another host port; the bed itself can only use 1883, so the machine that answers the bed's
DNS name must serve 1883.

## 5. HTTPS: reverse proxy or tunnel

At home, plain `http://` is fine. You need HTTPS when you want to reach the app away from home, install the
iPhone Home Screen app, or use browser notifications (Web Push only works on secure origins). Never port-forward
the plain HTTP port to the internet.

Whatever you use, set two things on the server:

- `ET_PUBLIC_URL=https://sleep.example.com`: how phones reach it. Notification buttons ("wait an hour") call back to this.
- `ET_TRUST_PROXY=on`: so sign-in rate limits see the visitor's address. Forwarding headers are believed only when
  the connection comes from a private or loopback address (your proxy), and Cloudflare's `CF-Connecting-IP` is read first.

The live view uses a WebSocket (`/api/live`), so the proxy must pass upgrade headers.

**First-time setup is refused through a proxy or tunnel on purpose.** Create the owner account once on the local
address (`http://192.168.1.20:8790`), then sign in from anywhere.

### Caddy

```caddyfile
sleep.example.com {
    reverse_proxy 192.168.1.20:8790
}
```

Caddy gets a certificate by itself and handles WebSockets. If Caddy runs on another machine, `ET_TRUST_PROXY=on` still
works because that machine has a private address.

### nginx

```nginx
server {
    listen 443 ssl http2;
    server_name sleep.example.com;
    ssl_certificate     /etc/ssl/sleep.example.com/fullchain.pem;
    ssl_certificate_key /etc/ssl/sleep.example.com/privkey.pem;

    location / {
        proxy_pass http://192.168.1.20:8790;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;       # WebSocket for the live view
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
    }
}
```

### Cloudflare Tunnel (or Tailscale Funnel and similar)

1. Install `cloudflared` on the server (or as a container next to it) and create a tunnel.
2. Add a public hostname, for example `sleep.example.com`, with the service `http://embertide:8790` (Compose service name)
   or `http://192.168.1.20:8790`.
3. Set `ET_PUBLIC_URL` and `ET_TRUST_PROXY=on`, restart the container.
4. Optional: put Cloudflare Access (or another login in front) as a second lock. The app has its own sign-in and
   uses it either way.

A private VPN (Tailscale, WireGuard) is the simplest option if only your own devices need to reach it: no public
address at all, and `http://` over the VPN is fine for everything except browser notifications.

## 6. Home Assistant

Ember & Tide publishes each person's side to Home Assistant over MQTT discovery: a thermostat, flash buttons, the
current phase, next change, in-bed sensor, and a number for each phase's temperature. No custom integration is needed.

1. In Home Assistant, install and start the **Mosquitto broker** add-on, and enable the **MQTT** integration.
   Create a Home Assistant user for Ember & Tide (Settings, People, Users) so it has its own login on that broker.
2. Give Ember & Tide the address. Either set `ET_HA_MQTT_URL=mqtt://user:password@192.168.1.10:1883` on the server,
   or open **Studio → Integrations → Home Assistant** and fill it in there.
3. The entities appear within seconds (`climate.embertide_<name>` and friends). **Publish again** and **Remove from
   Home Assistant** are in the same place.

Voice works through Home Assistant: expose the climate entity and flash buttons to your assistant ("set Alex's side to 68").
The ESPHome bedside dial also talks to Home Assistant; see **Studio → Integrations** (Dials) for the generated
configuration and setup steps, and `core/embertide/dial/` for the shared ESPHome package.

### Two brokers: the bed's and Home Assistant's

The bed's broker (this project's Mosquitto, port 1883, anonymous, only `orion/#`) and Home Assistant's broker (add-on,
with logins) are separate on purpose. Don't try to share one: the bed cannot log in, and you don't want an anonymous
listener on the broker that holds your Home Assistant credentials. If Home Assistant already uses port 1883 on the same
machine, move one of the two brokers to another host port, but remember the bed can only use 1883.

## 7. iPhone (Home Screen app)

The iPhone uses the web app as a Home Screen app. (A native iPhone app is planned, see `docs/IOS-PLAN.md`.)

1. Serve the app over HTTPS ([section 5](#5-https-reverse-proxy-or-tunnel)); set `ET_PUBLIC_URL`.
2. In Safari, open the address, sign in, then Share, **Add to Home Screen**.
3. Open Ember & Tide **from its Home Screen icon**. In More, Settings, Notifications, turn on notifications for this phone.

Notifications need iOS 16.4 or newer and only work for the Home Screen app, opened from its icon. The server
creates its Web Push key itself the first time it is needed (`vapid-private.pem` in the data folder). Not possible from a
web app on iPhone: Apple Health, background location and Bluetooth scanning for the bedside dial. Use the bed's own
sensors, a cloud wearable account (Oura, Withings, WHOOP, Polar) or a calendar feed instead.

## 8. First run

1. Open the server's **local address** on a device at home, for example `http://192.168.1.20:8790`. The first visit creates
   the **owner** account: a username and password.
2. The owner goes through a short guided setup, one decision per screen, each skippable and changeable later:
   name, how you sleep (Ember runs cold, Tide runs hot, or in between), colour and units, Simple or Full app, work days
   and bedtime and wake, Autopilot (Off, Suggest, Act; Suggest is the default), notifications, and your watch or ring.
   Settings, "Run first-time setup again" brings it back.
3. Invite the other sleeper: **Studio → People & access** makes a one-time link; nothing to install on their side.
   They go through the same guided setup for their own side.
4. Add devices you own in **Studio → Devices & calibration** (radar, scales, room sensors, calendar, bedside dial) when you like.
5. Put the bed on the network cutover in [Bed cutover](bed-cutover.md) when you are ready; Studio → Bed hub shows its health.

The first seven nights are a **calibration week**: Autopilot learns what a normal night looks like for each person and
stays quiet. After that it suggests small, explained changes (or makes them, if you chose Act).

Locked out? On the server: `docker exec -it ember-tide python -m embertide reset-password <username>` (or
`python -m embertide reset-password <username>` from source). It prints a one-time link, valid for 24 hours.

## 9. Upgrading, backups, restoring

**Nothing updates by itself.** To upgrade:

```bash
cd ember-tide
git pull                                  # or unpack the new release over the folder
docker compose up -d --build
```

The database upgrades its own tables on start. Read `CHANGELOG.md` first. Rolling back means checking out the old
version and running the same command, which works unless the newer version added data the older one cannot read; take a
backup before every upgrade.

**What to back up:** the data folder (`./data`, or the `/data` volume). The important files:

| File | What |
|---|---|
| `embertide.db` (plus `embertide.db-wal` and `-shm` while running) | all settings, rhythms, nights, accounts, devices |
| `vapid-private.pem` | the Web Push key; if lost, browsers and iPhones have to turn notifications on again |
| `fcm-service-account.json` | only if you set up Google push (your own file) |

The database is SQLite in WAL mode, so copy it consistently. Either stop the container for a moment:

```bash
docker compose stop embertide
cp -a data data-backup-$(date +%F)
docker compose start embertide
```

or take a live copy: `sqlite3 data/embertide.db ".backup 'backup.db'"`. Studio → Data export also gives you your nights as files.

**Restore:** stop the container, replace the contents of `./data` with the backup (all of the files above, including any
`-wal` and `-shm` files that were copied alongside), start it again. Phones stay signed in because sessions are in the database.

Keep a copy off the machine (another computer, cloud storage you trust). The database holds sleep data; treat the backup
like the original.

---

Next: [connect the bed](bed-cutover.md), [build the Android app](android.md), [configuration reference](configuration.md).
