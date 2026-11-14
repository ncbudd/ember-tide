# Installing Ember & Tide

This guide takes you from nothing to a running server, one small step at a time. You do not need to be an expert.
If a word is new, the [glossary](#glossary) at the end explains it in one line.

## Before you start

Ember & Tide is one small program (the **server**) that runs on a computer at home that is always on. Your phone,
a tablet, Home Assistant and the bed's hub all talk to it over your home network.

You need:

- [ ] **A computer that stays on all the time.** A NAS, a mini PC, a Raspberry Pi 4 or newer, or a spare laptop.
- [ ] **Docker** on that computer (the guide shows how to get it), or a NAS that already has it.
- [ ] **About 10 minutes** for the server, and another 10 for the bed.
- [ ] **For the real bed only:** an Orion Sleep topper, and either a personal API key from Orion's web app (easiest) or a router (or DNS server) where you can add one local DNS record (advanced, fully local).
- [ ] A **fixed address** for the server on your network. See [Give the server a fixed address](#give-the-server-a-fixed-address).

You can install the server first and look around with the built-in **simulator**, and connect the bed later.
Nothing here updates itself, and nothing needs an account with anyone.

## Which way should I install?

Pick the row that matches what you already own. If you are unsure, use the Linux or Raspberry Pi steps; they work almost everywhere.

| I have... | Go to |
|---|---|
| **Unraid** | [Unraid](#unraid) |
| **Synology** (DSM 7.2 or newer) | [Synology](#synology) |
| **TrueNAS SCALE** | [TrueNAS SCALE](#truenas-scale) |
| **Proxmox** | [Proxmox](#proxmox) |
| A **Raspberry Pi**, a mini PC or any **Linux** machine | [Docker Compose on Linux or a Raspberry Pi](#docker-compose-on-linux-or-a-raspberry-pi) |
| A **Windows** PC or a **Mac** | [Windows and Mac](#windows-and-mac) |
| **Portainer** | [Portainer](#portainer) |
| **CasaOS**, **Umbrel**, **Cosmos**, **Runtipi** or similar | [CasaOS and similar](#casaos-umbrel-and-similar) |
| **Home Assistant OS** (a Pi or a VM running only Home Assistant) | [Home Assistant](#home-assistant) |
| Just curiosity, no bed | [Try it without a bed](#try-it-without-a-bed) |
| I write code | [Advanced: plain Docker and running from source](#advanced-plain-docker-and-running-from-source) |

Every method ends the same way: open the app, do the [first-run setup](#b-first-run-setup-in-the-app), then
[point the bed at your server](#c-point-the-bed-at-your-server).

---

## The parts every method shares

Read this section once. The platform sections below send you back here instead of repeating it.

### A. The Compose file and the broker

Ember & Tide ships a Compose file, `docker-compose.example.yml`. **Compose** is a text file that tells Docker which programs to start.
Ours defines two services; only the first starts by default:

| Service | Image | What it does |
|---|---|---|
| `embertide` | the ready-made image `ghcr.io/ncbudd/ember-tide` (web app and server in one image; amd64 and arm64) | the app, Studio and the API on port **8790**. Its data is the folder `./data` next to the Compose file. A tiny `data-init` helper runs first and prepares the folder. |
| `mosquitto` (profile `local-bed`) | `eclipse-mosquitto:2.0.20` | the **MQTT broker** the bed talks to, on port **1883**. It starts only when you ask for it with `docker compose --profile local-bed up -d`, so simulator and cloud users never run a broker. |

Docker **pulls the ready-made image** the first time, which takes a minute. You still need the project folder for the Compose file and
the broker's settings in `deploy/mosquitto/`; the app itself is not built on your machine. (To build the image yourself instead, see
[Advanced: build from source](#advanced-plain-docker-and-running-from-source).)

**Get the project folder.** Either of these gives you the same thing:

```bash
git clone https://github.com/Ncbudd/ember-tide.git
```

or download the project as a ZIP from the repository page and unpack it. The folder should look like this:

```text
ember-tide/
  Dockerfile
  docker-compose.example.yml
  deploy/mosquitto/mosquitto.conf
  deploy/mosquitto/acl
  core/  web/  docs/ ...
```

**Make your copy of the Compose file.** From inside the folder:

```bash
cp docker-compose.example.yml docker-compose.yml
```

**Set your time zone** in `docker-compose.yml` (the `TZ` line), for example `America/New_York`, `America/Chicago`, `America/Denver`,
`America/Los_Angeles`, `Europe/London` or `Australia/Sydney` (the [list of names](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)). Schedules use local time, so a wrong
zone means a wrong bedtime.

**Start it:**

```bash
docker compose up -d
docker compose ps
```

After a minute or two, `docker compose ps` should show the app as **healthy**. (`data-init` shows as Exited: that is normal.) (When you later use the real bed through the bundled broker,
start with `docker compose --profile local-bed up -d`, or put `COMPOSE_PROFILES=local-bed` in a `.env` file next to the Compose file so
plain `docker compose up -d` includes it.)

**Folder permissions.** Nothing to do for a new install: a small helper prepares the `data` folder for you. If the app ever says it cannot write there, see [the fix](troubleshooting.md#the-data-folder-cannot-be-written-synology-unraid-nas).

**Check that it worked.** Open `http://<server-ip>:8790` on a phone or computer on your home network
(for example `http://192.168.1.10:8790`). You should see the welcome screen asking you to create the owner account.
For a plain yes or no, open `http://<server-ip>:8790/healthz`; you should see a short reply with the version.

**Why the broker matters.** The bed's hub can only talk to a broker on port **1883**, and it cannot log in.
So the Compose file publishes the broker on the machine's own port 1883, and allows anonymous connections, but only on
the bed's two topics (`orion/<serial>/in` and `orion/<serial>/out`, set in `deploy/mosquitto/acl`, which matches any serial number, so you never edit it).
Two rules follow from that:

- **The machine's port 1883 must be free.** Another broker already using it is the most common problem
  ([fix](troubleshooting.md#port-1883-is-already-in-use)).
- **Keep 1883 on your home network.** Never forward it on your router.

If you use the broker for the bed, keep the Mosquitto version at **2.0.20**. Version 2.0.21 changed keep-alive handling in a way the bed's
firmware does not like, and the bed drops off now and then. The example file already pins the right one.

**Other choices in the same file.** A line that starts with `#` is switched off. To switch, put `# ` in front of the old line, and remove the `# `
(just those two characters; keep the spaces before them) from the lines you want. Then run `docker compose up -d` again:

| I want... | In the file |
|---|---|
| The simulator (the default) | leave `ET_BED: sim` |
| The real bed, with a personal key from the maker's web app (easiest) | `ET_BED: orion-cloud` and `ET_ORION_API_KEY` (no broker needed) |
| Advanced: the real bed, fully local | `ET_BED: orion-mqtt` and `ET_MQTT_URL: mqtt://mosquitto:1883`, then `docker compose --profile local-bed up -d` |
| A bed of your own | `ET_BED: python:package.module:ClassName` ([drivers.md](drivers.md)) |
| A different web port | change the **left** number in `"8790:8790"`, for example `"8791:8790"` |
| Home Assistant | `ET_HA_MQTT_URL: mqtt://user:pass@<home-assistant-ip>:1883` ([details](#home-assistant)) |
| Secrets out of the file | put them in a `.env` file and add `env_file: .env` ([details](configuration.md#environment-variables)) |

Every setting is described in [configuration.md](configuration.md).

**Where the paths are not "next to the file."** Some tools (Unraid, Portainer, TrueNAS, CasaOS) store your pasted Compose text
somewhere of their own, so the relative paths `.` and `./data` would point to the wrong place. On those, use full paths. Using
`/srv/embertide` as the example project folder:

| In the Compose file | Change it to |
|---|---|
| `./data:/data` | `/srv/embertide/data:/data` |
| `./deploy/mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro` | `/srv/embertide/ember-tide/deploy/mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro` |
| `./deploy/mosquitto/acl:/mosquitto/config/acl:ro` | `/srv/embertide/ember-tide/deploy/mosquitto/acl:/mosquitto/config/acl:ro` |

Each platform section says which folder to use in place of `/srv/embertide`.

**Already have a broker?** Just do not use the `local-bed` profile (or delete the `mosquitto` service), and set `ET_MQTT_URL` to your broker.
The bed still needs a listener on port 1883 without a login; copy the two `topic` lines from `deploy/mosquitto/acl` into that broker's access list.

### B. First-run setup in the app

1. On a device at home, open `http://<server-ip>:8790`. You should see the welcome screen.
2. Create the **owner** account (a username and a password). This only works from your home network, on the server's own
   address, never through a proxy or tunnel. That is on purpose.
3. Follow the short setup: one question per screen, each skippable and changeable later (name, how you sleep, units, work days,
   bedtime and wake time, Autopilot, notifications, watch or ring). On a phone it starts by itself; on a computer, the app opens
   Studio, where a **Start setup** button takes you there. **Settings, Run first-time setup again** brings it back.
4. Invite the other sleeper: **Studio, People & access** makes a one-time link. Nothing to install on their side.
5. Add things you own (radar, scales, room sensors, calendar, bedside dial) in **Studio, Devices & calibration**, whenever you like.

The first seven nights are a **calibration week**: Autopilot watches and stays quiet, then it starts suggesting small, explained changes.

Locked out later? On the server, run `docker exec -it ember-tide python -m embertide reset-password <username>`. It prints a one-time link, valid for 24 hours.

### C. Point the bed at your server

When the server works with the simulator and you are ready for the real bed, pick one way.

**Option 1: the maker's cloud with your own API key (easiest).** No network changes, and the maker's own app keeps working.
It checks the bed about every 10 seconds, so it is a little slower.

1. Sign in to Orion's web app at https://app.orionsleep.com/api-keys (the same account as your Orion phone app), create a personal API key and copy it. (Pages can move; look for "API keys".)
2. In `docker-compose.yml`, switch to the cloud lines: `ET_BED: orion-cloud` and `ET_ORION_API_KEY: <your key>`, pasted as it is, no quotes needed (see "Other choices" above for how to switch lines).
   Then apply it: in a terminal, `docker compose up -d`; on **Synology**, edit the file in File Station, then in Container Manager open **Project**, `embertide`, **Action**, **Build**; on a pasted-in stack (Portainer, Unraid), edit the stack and redeploy. Do not share this key or post it anywhere.

**Option 2, advanced: fully local.** Faster and works without the internet, but it needs a local DNS record, and the maker's cloud features stop while it is in place.

1. In `docker-compose.yml`, switch to the bed lines: `ET_BED: orion-mqtt` and `ET_MQTT_URL: mqtt://mosquitto:1883`. Then run `docker compose --profile local-bed up -d` so the bundled broker starts too.
   (On a pasted-in stack such as Portainer, Unraid, TrueNAS or CasaOS, there is no profile option: delete the line `profiles: ["local-bed"]` from the `mosquitto` service, then redeploy, or the broker never starts.)
2. Add one local DNS record so the bed's usual name points at your server, and power-cycle the bed.

For option 2, [Bed cutover](bed-cutover.md) shows both steps for UniFi, Pi-hole, AdGuard Home, dnsmasq, OpenWrt and OPNsense, how to check it
worked, how to block the hub's internet, and how to **roll back** at any time.

### D. HTTPS: phones away from home and iPhone notifications

At home, plain `http://` is fine. Opening the app in Safari or any browser at `http://<server-ip>:8790` works, and Add to Home Screen
may work as a shortcut (it then opens like a website, without notifications). You need HTTPS for browser and iPhone **notifications**
(they only work on secure addresses) and to use the app **away from home**. **Never port-forward the web port to the internet.**

**Simplest recipe for most people: Tailscale.** It is a private network between your own devices, with no public address. Install it on the server and on your phone,
then open the server's Tailscale address. That reaches the app away from home over `http://`. For notifications you also need `https://`, which Tailscale may
provide with its **Serve** feature (see Tailscale's own guide for `tailscale serve`). Then set `ET_PUBLIC_URL` to the `https://` address it shows. The options below are for people who already run a web server or a domain.

If you reach it through an `https://` address (Tailscale Serve, a reverse proxy or a tunnel), set two things on the server (in the `environment:` part of the Compose file). Plain `http://` over a VPN needs neither:

- `ET_PUBLIC_URL: https://sleep.example.com`, which is how phones reach it. Notification buttons call back to this address.
- `ET_TRUST_PROXY: "on"`, so sign-in limits see the visitor's real address.

The live view uses a WebSocket (`/api/live`), so a proxy must pass upgrade headers. Easiest options, from simplest:

- **A private VPN such as Tailscale or WireGuard** (see above). No public address at all; `http://` over the VPN works for everything except browser notifications.
- **Caddy** (gets its own certificate, handles WebSockets):

  ```caddyfile
  sleep.example.com {
      reverse_proxy 192.168.1.10:8790
  }
  ```

- **nginx:**

  ```nginx
  server {
      listen 443 ssl http2;
      server_name sleep.example.com;
      ssl_certificate     /etc/ssl/sleep.example.com/fullchain.pem;
      ssl_certificate_key /etc/ssl/sleep.example.com/privkey.pem;

      location / {
          proxy_pass http://192.168.1.10:8790;
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

- **Cloudflare Tunnel** (or Tailscale Funnel): install `cloudflared` on the server, create a tunnel, add a public hostname such as
  `sleep.example.com` with the service `http://192.168.1.10:8790`, set the two variables above (plus `ET_PROXY_CLOUDFLARE: "on"` for Cloudflare), and restart the container. You can add
  Cloudflare Access as a second lock; the app has its own sign-in either way.

**iPhone.** The iPhone uses the web app as a Home Screen app (a native app is planned, see `docs/IOS-PLAN.md`):

1. Serve the app over HTTPS and set `ET_PUBLIC_URL`.
2. In Safari, open the address, sign in, then Share, **Add to Home Screen**.
3. Open Ember & Tide **from its Home Screen icon**, then More, Settings, Notifications, and turn notifications on for this phone.

This needs iOS 16.4 or newer. Not possible from a web app on iPhone: Apple Health, background location and Bluetooth scanning for the bedside dial.
Use the bed's own sensors, a cloud wearable account (Oura, Withings, WHOOP, Polar) or a calendar feed instead. For Android, see [android.md](android.md).

**Host it on its own address.** Give Ember & Tide its own (sub)domain or its own port (`sleep.example.com`, or
`https://home.example.com:8790`). Serving it under a path such as `https://example.com/embertide` is not supported: the web app
and its API assume they live at the root.

**Which proxy may be believed.** With `ET_TRUST_PROXY: "on"`, forwarding headers are believed from any private or loopback
address by default. To narrow that to your actual proxy, list it in `ET_TRUSTED_PROXIES`, for example
`ET_TRUSTED_PROXIES: "172.18.0.0/16"` (your Docker network) or `"127.0.0.1, 192.168.1.5"`. Several entries are separated by commas.

**Sign in through your own login portal (optional).** If Authelia, Authentik, Cloudflare Access or Tailscale Serve already signs
people in, Ember & Tide can trust the username they pass along instead of asking for a second password. Off by default.

```yaml
      ET_TRUST_PROXY: "on"
      ET_TRUSTED_PROXIES: "172.18.0.0/16"     # required: only your proxy may name users
      ET_AUTH_HEADER: "Remote-User"
```

Both `ET_TRUST_PROXY: "on"` and `ET_TRUSTED_PROXIES` are required here (the server will refuse to start with forward-auth
if `ET_TRUSTED_PROXIES` is empty). The header is believed only when the connection comes from a listed proxy, and it only ever signs in an **existing** account
with that username (not case-sensitive); someone with no account gets a plain "ask the owner to add you" message, and nothing
is created automatically. Create the accounts first (owner setup, then People in Studio). The password sign-in still works
as a fallback. Make sure your proxy removes any copy of the header the visitor sends and sets its own; the ones below do that.

| Proxy | Header to use |
|---|---|
| Authelia (forward auth) | `ET_AUTH_HEADER: Remote-User` (also sends `Remote-Email`, `Remote-Name`, `Remote-Groups`); copy them with `authResponseHeaders` / `auth_request_set`. |
| Authentik (proxy outpost) | `ET_AUTH_HEADER: X-Authentik-Username` (and `X-Authentik-Email`). |
| Cloudflare Access | Email only: `ET_AUTH_HEADER_EMAIL: Cf-Access-Authenticated-User-Email` (plus `ET_PROXY_CLOUDFLARE: "on"` and `ET_TRUSTED_PROXIES`). The account's username must be that email address. |
| Tailscale Serve | `ET_AUTH_HEADER_EMAIL: Tailscale-User-Login` (a login such as `alex@example.com`); only present on tailnet traffic that `tailscale serve` proxies to `127.0.0.1`, so set `ET_TRUSTED_PROXIES: "127.0.0.1"` and keep the web port on loopback only: with Docker, publish it as `"127.0.0.1:8790:8790"` (do not set `ET_HOST` inside the container); without Docker, `ET_HOST: 127.0.0.1`. |

Usernames in Ember & Tide are plain words; if yours differ from what the portal calls people, make the account name match
(or use a portal that can send the name you want). The app itself (phones with the Android app, dials, watches) keeps using
its own sign-in and keys.

### E. Updating and backups

**Nothing updates by itself.** To update, in the project folder:

```bash
docker compose pull                   # fetches the newest image (or the version you pinned)
docker compose up -d
```

If you changed `docker-compose.yml` to a pinned tag such as `:0.1.0`, edit the tag first. Update the project folder too (`git pull`, or
unpack the new release over it) when you want new example files or broker settings.

On a pasted-in stack (Unraid, Portainer, ...), use that tool's **Update stack** or **Compose Up** button with "re-pull image" turned on. Read `CHANGELOG.md` first. The database upgrades its own
tables on start. Rolling back means checking out the old version and running the same command; it works unless the newer version added data
the older one cannot read, so **take a backup before every update.**

**What to back up:** the data folder (`./data`, or `/data` inside the container). It holds:

| File | What |
|---|---|
| `embertide.db` (plus `embertide.db-wal` and `-shm` while running) | all settings, rhythms, nights, accounts, devices |
| `vapid-private.pem` | the Web Push key. If lost, phones have to turn notifications on again |
| `fcm-service-account.json` | only if you set up Google push (your own file) |

The easy, safe backup stops the app for a moment:

```bash
docker compose stop embertide
cp -a data data-backup-$(date +%F)
docker compose start embertide
```

A live copy also works, with nothing extra installed: `docker exec ember-tide python -c "import sqlite3; sqlite3.connect('/data/embertide.db').backup(sqlite3.connect('/data/backup.db'))"` writes `data/backup.db`. Studio, Data export gives you your nights as files too.

**Restore:** stop the container, replace the contents of `./data` with the backup (including any `-wal` and `-shm` files that were copied
with it), start it again. Phones stay signed in. Keep one copy on another machine; the database holds sleep data, so treat the copy like the original.

---

## Docker Compose on Linux or a Raspberry Pi

Works on Raspberry Pi OS (64-bit), Debian, Ubuntu, Fedora and most others.

1. **Install Docker**, if it is not there yet. Check with `docker compose version`; if you see a version number, skip to step 2.

   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER
   ```

   Log out and back in so the group change takes effect. Then `docker compose version` should print a version.

2. **Get the project** into a folder you will keep, for example your home folder:

   ```bash
   cd ~
   git clone https://github.com/Ncbudd/ember-tide.git
   cd ember-tide
   ```

   No `git`? Install it with `sudo apt install git`, or download the ZIP as described in [A](#a-the-compose-file-and-the-broker).

3. **Copy the Compose file and set your time zone** (the `TZ` line):

   ```bash
   cp docker-compose.example.yml docker-compose.yml
   nano docker-compose.yml
   ```

   In `nano`, save with `Ctrl+O`, Enter, then leave with `Ctrl+X`.

4. **Start it:**

   ```bash
   docker compose up -d
   docker compose ps
   ```

   The first start pulls the image, which takes a minute or two. You should now see `ember-tide`, and after a minute it says **healthy**. A line for `data-init` that says Exited is normal: that helper runs once and stops.

5. **Open it.** Find the machine's address with `hostname -I`, then open `http://<server-ip>:8790`. You should see the welcome screen.
   Continue with [B. First-run setup](#b-first-run-setup-in-the-app).

To read the log: `docker compose logs -f embertide` (press `Ctrl+C` to stop watching). The data is in the `data` folder next to the Compose file.

## Unraid

Unraid has no Community Applications template for Ember & Tide yet, so you use the **Compose Manager** plugin, which runs a Compose file from the Docker tab.

**Before you start:** make sure nothing else on the server uses host port **1883** (an existing Mosquitto container, for example) or **8790**.
Open the Docker tab and look at the Port Mappings column.

1. **Install Compose Manager.** In Unraid, open **Apps** (Community Applications), search for **Compose Manager**, click **Install**.
   You should now see a **Compose** section under the Docker tab.
2. **Get the project into appdata.** Open the Unraid **Terminal** (the `>_` icon, top right) and run:

   ```bash
   mkdir -p /mnt/user/appdata/embertide
   cd /mnt/user/appdata/embertide
   git clone https://github.com/Ncbudd/ember-tide.git
   mkdir -p data
   ```

   If the terminal says `git: command not found`, download the ZIP from the repository page on another computer, copy it into the
   `appdata` share over the network (`\\<server-name>\appdata`), and unpack it so that the folder `/mnt/user/appdata/embertide/ember-tide` contains
   `Dockerfile` and `deploy`. You should now see `Dockerfile` when you run `ls /mnt/user/appdata/embertide/ember-tide`.
3. **Add a stack.** On the Docker tab, scroll to **Compose**, click **Add New Stack**, name it `embertide`, and click **OK**.
   Click the gear icon beside the stack, then **Edit Stack**, then **Compose File**.
4. **Paste the Compose file.** Open `docker-compose.example.yml` from the project (you can `cat` it in the terminal), copy it all and paste it in. Then make these changes
   (this is the "full paths" table from [A](#a-the-compose-file-and-the-broker), with the Unraid folder filled in):

   - `./data:/data` becomes `/mnt/user/appdata/embertide/data:/data`
   - the two Mosquitto lines become `/mnt/user/appdata/embertide/ember-tide/deploy/mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro` and `/mnt/user/appdata/embertide/ember-tide/deploy/mosquitto/acl:/mosquitto/config/acl:ro`
   - set `TZ` to your time zone

   Click **Save Changes**.
5. **Compose Up.** Click **Compose Up** beside the stack. A window shows the image being pulled; the first time it takes a minute or two. When it says it is done, close it.
   You should now see the container `ember-tide` in the Docker list. (`ember-tide-mosquitto` appears only once you use the real bed through the
   bundled broker: Unraid's Compose Up has no profile option, so for that remove the line `profiles: ["local-bed"]` from the `mosquitto` service first.)
   If the data folder cannot be written, add `user: "99:100"` under the `embertide:` service (Unraid's own owner for shares), or run `chown -R 1000:1000 /mnt/user/appdata/embertide/data` once.
6. **Open it.** Open `http://<unraid-ip>:8790` (the address you use for the Unraid web page, with `:8790`). You should see the welcome screen.
   Continue with [B. First-run setup](#b-first-run-setup-in-the-app).

**Good to know on Unraid**

- **Port clash.** If something else already uses port 8790, change only the **left** number in `"8790:8790"`, for example `"8791:8790"`, and open
  `http://<unraid-ip>:8791`. Do not change the broker's `"1883:1883"`: the bed can only use port 1883, so the Unraid server must own it. If another
  container holds 1883, stop it or give *that* one a different host port.
- **About networking.** The broker uses Docker's normal bridge network and publishes port 1883 on the Unraid server's address, which is
  the same address the bed's DNS record points at. You do not need host networking. The app reaches the broker by its service name (`mosquitto`).
- **Appdata.** Everything you need to back up is `/mnt/user/appdata/embertide/data`. Include it in your appdata backup.
  If the database cannot be written, see [permissions](troubleshooting.md#the-data-folder-cannot-be-written-synology-unraid-nas).
- **Logs.** Click the container's icon on the Docker tab, then **Logs**. Or in the terminal: `docker logs -f ember-tide` and `docker logs ember-tide-mosquitto`.
- **Updating.** In the terminal, **Compose Pull**, then **Compose Up** again on the stack (see [E](#e-updating-and-backups)).

## Synology

Needs **DSM 7.2 or newer** with **Container Manager** (install it from Package Center). On older DSM, the package is called Docker and has no Project feature.

1. **Make the folders.** In **File Station**, open the shared folder `docker` (create it in Control Panel, Shared Folder, if it is missing), then create the folder
   `embertide` inside it. You should now have `/volume1/docker/embertide`.
2. **Put the project in that folder.** On your computer, download the project ZIP ([direct link](https://github.com/ncbudd/ember-tide/archive/refs/heads/main.zip)) and unpack it. You get a folder called `ember-tide-main`. Open it and upload what is **inside** it (not the folder itself) into `docker/embertide` with File Station (drag and drop). `Dockerfile`, `docker-compose.example.yml` and the folder `deploy` should now sit directly in `docker/embertide`.
3. **Rename and edit the Compose file.** In File Station, copy `docker-compose.example.yml` and name the copy `docker-compose.yml` (or just rename it). Right-click it, **Open with Text Editor**, set `TZ` to your time zone, and save.
4. **Create the project.** Open **Container Manager**, **Project**, **Create**. Project name `embertide`. Path: click Set Path and choose `/volume1/docker/embertide`.
   Source: **Use existing docker-compose.yml** (it detects your file). Click **Next**, skip the web portal page, **Next**, then **Done**.
5. **Wait for the download.** The first time it takes a minute or two. You should now see the project `embertide` as **Running**. Only `ember-tide` stays running; `data-init` shows **Exited** or **Stopped**, which is normal (it runs once and stops).
6. **Open it.** Open `http://<nas-ip>:8790`. Your NAS's address (`<nas-ip>`) is the one in your browser's address bar when you open DSM, or in DSM under Control Panel, Network. You should see the welcome screen. Continue with [B. First-run setup](#b-first-run-setup-in-the-app).

**Good to know on Synology**

- The data is `/volume1/docker/embertide/data`. Add it to Hyper Backup.
- **The broker** (`mosquitto`) does not run by default; you only need it for the advanced fully local bed method. To enable it later, edit `docker-compose.yml` and delete the line `profiles: ["local-bed"]` in the `mosquitto` service, then Container Manager, Project, `embertide`, **Action**, **Build**.
- Port 1883 is normally free on a Synology. If you also run Mosquitto, Home Assistant or similar there, see
  [port 1883 in use](troubleshooting.md#port-1883-is-already-in-use).
- If the app reports it cannot open its database, see [permissions](troubleshooting.md#the-data-folder-cannot-be-written-synology-unraid-nas).
- **Logs:** Container Manager, **Container**, click `ember-tide`, **Log**.
- **Updating:** upload the new project files over the folder, then Project, `embertide`, **Action**, **Pull**, then **Build** (it restarts with the new image).
- If the NAS firewall is on (Control Panel, Security, Firewall), allow TCP ports 8790 and 1883 from your home network.

## TrueNAS SCALE

TrueNAS SCALE changed how apps work in 24.10 (it moved to Docker), and the Apps screens differ between versions. Use whatever your version offers for a
custom app from YAML, and keep to this short recipe. The result is the same Compose file.

1. **Make a dataset** for it (Datasets, Add Dataset), for example `apps/embertide`, and note its path, such as `/mnt/tank/apps/embertide`.
2. **Get the project there.** Open **System, Shell** (or SSH) and run:

   ```bash
   cd /mnt/tank/apps/embertide
   git clone https://github.com/Ncbudd/ember-tide.git
   mkdir -p data
   ```

3. **Nothing to build.** The Compose file uses the ready-made image `ghcr.io/ncbudd/ember-tide`, which TrueNAS pulls itself.

4. **Install via YAML.** In **Apps**, choose **Discover Apps**, then **Custom App** (on 25.04 and later: **Install via YAML**). Paste the contents of
   `docker-compose.example.yml`, then change it with the table in [A](#a-the-compose-file-and-the-broker): keep the `image:` line and use the
   full dataset paths for the data folder and the two Mosquitto files (for example `/mnt/tank/apps/embertide/ember-tide/deploy/mosquitto/acl`). Set your time zone and save.
5. **Open it.** Open `http://<truenas-ip>:8790`. You should see the welcome screen. Continue with [B. First-run setup](#b-first-run-setup-in-the-app).

On TrueNAS, the web interface itself uses ports 80 and 443, so 8790 and 1883 are normally free. If the Apps screens in your version do not
accept Compose YAML, use a small VM or the Proxmox-style Debian machine described below and follow the Linux steps instead. For updates, pull the image again (or change the tag) and restart the app.

## Proxmox

The cleanest way is a small Debian machine just for this, with Docker inside it. Either kind works:

- **A VM** (simplest, fewest surprises): create a Debian 12 VM with 1 CPU core, 1 GB of RAM and 8 GB of disk, install Debian with SSH, and go on to step 3.
- **An LXC container** (lighter): create a Debian 12 container (Create CT) with 1 core, 1 GB of RAM, 8 GB of disk, **Unprivileged container ticked**, and a network
  bridge with a fixed address (or a DHCP reservation).

1. **For an LXC only: turn on nesting.** Docker needs it. Select the container, **Options, Features, Edit**, tick **Nesting** (and **keyctl** if offered), and OK.
   Start the container. If you forget, Docker fails with permission errors ([LXC fix](troubleshooting.md#docker-will-not-start-in-a-proxmox-lxc)).
2. **Open the container's console** (or SSH in) as root.
3. **Install the basics and Docker:**

   ```bash
   apt update && apt install -y curl git
   curl -fsSL https://get.docker.com | sh
   ```

4. **Follow the Linux steps** from step 2 of [Docker Compose on Linux or a Raspberry Pi](#docker-compose-on-linux-or-a-raspberry-pi). You should now see `ember-tide` healthy.
5. **Open it** at `http://<machine-ip>:8790`. Give the VM or container a fixed address, since the bed's DNS record will point at it.

## Portainer

Portainer can run the Compose file as a **stack**. It needs the project folder on the Docker host for the broker's settings files; the app comes as a ready-made image.

1. **Put the project on the Docker host.** On the host's terminal:

   ```bash
   sudo mkdir -p /srv/embertide && cd /srv/embertide
   sudo git clone https://github.com/Ncbudd/ember-tide.git
   sudo mkdir -p data
   ```

2. In Portainer, choose your environment, **Stacks, Add stack**. Name it `embertide`.
3. Choose **Web editor** and paste the contents of `docker-compose.example.yml`.
4. Change the paths with the table in [A](#a-the-compose-file-and-the-broker) (data folder `/srv/embertide/data`, and the two
   Mosquitto files under `/srv/embertide/ember-tide/deploy/mosquitto/`). Set `TZ`.
5. Click **Deploy the stack**. You should now see the stack with `ember-tide` running (`data-init` exits after a moment; that is normal).
6. **Open it** at `http://<host-ip>:8790`. Logs: Containers, `ember-tide`, **Logs**.

To update: in Portainer open the stack and click **Update the stack** with **Re-pull image** turned on.

## CasaOS, Umbrel and similar

These are friendly front ends for Docker. Ember & Tide is not in their app stores, so use their custom install.

- **CasaOS:** open the SSH terminal (or the CasaOS terminal), get the project as in the [Portainer](#portainer) steps, then in the CasaOS dashboard choose
  **App Store, Custom Install, Import** and paste the Compose file after changing the paths. Open the app at `http://<casaos-ip>:8790`.
- **Cosmos, Runtipi, Yacht and similar:** they accept a Compose file in the same way. Use the same recipe: project folder on the host, paste the file with full paths.
- **Umbrel:** apps come from its own store and a custom Compose file is not supported without extra work. The simplest route is to run Ember & Tide on another machine
  with the Linux steps.

Whichever you use, the web port is **8790** and the broker must own the host's port **1883**.

## Home Assistant

**Home Assistant OS has no Ember & Tide add-on.** Run Ember & Tide on another machine (any section above), and connect the two over MQTT. Home Assistant stays exactly as it is.

Ember & Tide publishes each person's side to Home Assistant through MQTT discovery: a thermostat, flash buttons, the current phase, next change, an in-bed sensor,
and a number for each phase's temperature. No custom integration is needed.

1. In Home Assistant, install and start the **Mosquitto broker** add-on (Settings, Add-ons) and add the **MQTT** integration.
2. Create a Home Assistant user for Ember & Tide (Settings, People, Users, Add user) so it has its own login.
3. Give Ember & Tide the address. Either set `ET_HA_MQTT_URL: mqtt://user:password@192.168.1.5:1883` in the Compose file (use Home Assistant's address and the user from step 2),
   or open **Studio, Integrations, Home Assistant** in the app and fill it in there.
4. You should now see entities within seconds: `climate.embertide_<name>` and friends. **Publish again** and **Remove from Home Assistant** are on the same Studio page.

Voice works through Home Assistant: expose the climate entity and flash buttons to your assistant ("set Alex's side to 68"). [Talk to your bed](voice.md)
has the steps for Siri (HomeKit Bridge), Google Assistant, Alexa and Assist, and Apple Shortcuts for iPhones without Home Assistant. The ESPHome bedside dial also
goes through Home Assistant; **Studio, Integrations** (Dials) shows the generated configuration.

**Keep two brokers.** The bed's broker (this project's Mosquitto, anonymous, only the two `orion/...` topics) and Home Assistant's add-on broker (with logins) are separate
on purpose. The bed cannot log in, and you do not want an anonymous listener on the broker that holds your Home Assistant logins. So do not point the bed at the Home
Assistant add-on, and do not put Home Assistant's login on the bed's broker. If you already run Mosquitto elsewhere and want it to serve the bed, add an anonymous listener
on port 1883 limited to `orion/+/in` and `orion/+/out`, as in `deploy/mosquitto/mosquitto.conf` and `deploy/mosquitto/acl`.

If both brokers would sit on one machine and both want port 1883, give the Home Assistant one another host port; the bed can only use 1883.

## Windows and Mac

Docker Desktop is fine for **trying it out**. For the real bed, the computer must stay on, awake and connected, with a fixed address. A small always-on machine is better for that.

1. **Install Docker Desktop** from docker.com, open it once, and wait until it says it is running. (On Windows, accept the WSL 2 option if it asks.)
2. **Get the project.** Download the ZIP (https://github.com/ncbudd/ember-tide/archive/refs/heads/main.zip) and unpack it, or use `git clone` if you have Git. Go into the folder that holds `docker-compose.example.yml` (Windows' Extract All can put it one level down, inside a second `ember-tide-main` folder). Open a terminal there: on Windows, in File Explorer, right-click empty space in that folder, then **Open in Terminal**. On a Mac, right-click the folder in Finder, then **New Terminal at Folder** (if you do not see it, turn it on in System Settings, Keyboard, Keyboard Shortcuts, Services).
3. **Copy the Compose file and set your time zone:**

   ```bash
   cp docker-compose.example.yml docker-compose.yml        # Windows PowerShell: copy docker-compose.example.yml docker-compose.yml
   ```

   Open `docker-compose.yml` in a text editor and set `TZ` (on Windows: `notepad docker-compose.yml`; on a Mac: `open -e docker-compose.yml`).
4. **Start it:**

   ```bash
   docker compose up -d
   ```

5. **Open it** at `http://localhost:8790` on that computer. You should see the welcome screen. From a phone, use the computer's address (below).

**Windows firewall.** If Windows asks to allow Docker Desktop, click **Allow** on private networks. If other devices still cannot open the app, see [the fix](troubleshooting.md#other-devices-cannot-reach-the-app-on-windows).

Your network must be set to **Private** (Settings, Network). Find the computer's address with `ipconfig` (Windows: use the **IPv4 Address** under **Wi-Fi** or **Ethernet**, usually `192.168.x.x`; ignore `vEthernet` adapters, which phones can't reach) or System Settings, Network (Mac).

**Mac:** if macOS asks to allow incoming connections for Docker, choose Allow. Also stop the Mac from sleeping (System Settings, Battery or Energy) if it runs the bed.

## Try it without a bed

You do not need the bed to look around. With the default `ET_BED: sim`, the server runs a simulated bed with scripted sleepers.

1. Start the server with any method above.
2. Open `http://<server-ip>:8790`, create the owner account and finish the short setup.
3. Open **Studio, Simulator**. You can jump to bedtime, speed up the clock and put people in or out of bed.

To run the clock faster from the start, set `ET_SIM_SPEED` (for example `60` runs a minute per second); it is also a control in Studio, Simulator.
When you are ready for the real bed, do [C. Point the bed at your server](#c-point-the-bed-at-your-server).

## Give the server a fixed address

The bed's DNS record points at your server's address, so that address must not change. Ask your router to always hand the same address to the server. The list below says where to click in common routers. This is
called a **DHCP reservation** (or "fixed IP", "static lease" or "address reservation"). Do the same for the bed's hub; it makes the hub easy to find later.

Where to look, in plain words:

- **UniFi:** Client Devices, click the device, Settings, tick **Fixed IP Address**, pick the address, Apply.
- **eero:** the eero app, Settings, Network Settings, **Reservations & port forwards**, Add a reservation.
- **TP-Link Deco:** the Deco app, More, Advanced, **Address Reservation**.
- **Google Wifi or Nest Wifi:** the Google Home app, Wi-Fi, Settings, Advanced networking, **DHCP IP reservations**.
- **ASUS:** router page, LAN, **DHCP Server**, Manually Assigned IP around the DHCP list.
- **Anything else:** in the router's page, look for "DHCP reservation", "static lease" or "address binding" under LAN or DHCP.

Simply reserve the server's current address in your router app, and write it down; it is the `<server-ip>` used throughout this guide. To find it: on a Synology, Control Panel, Network (or the address in the browser bar when you open DSM); on Linux, `hostname -I`; on Windows, `ipconfig`; on a Mac, System Settings, Network.

## Without Docker (Linux, Raspberry Pi)

For a machine where you would rather not run Docker. You need Python 3.12 or newer, Node 20 or newer (only to build the web app once),
and `git`. The published Docker image is multi-arch (`linux/amd64` and `linux/arm64`), so a 64-bit Raspberry Pi (Pi 4 or 5 with the
64-bit OS) runs the Docker route too; the steps below work on any Linux, including a Pi. On a Pi, building the web app takes a few minutes.

Before step 1, get the tools. On Ubuntu and Debian the Python virtual-environment module is a separate package, so `python3 -m venv`
fails with "ensurepip is not available" until you run `sudo apt install python3.12-venv` (use the version your system has, and `python3-pip` if you
want `pip` in the environment). Ubuntu's own `nodejs` package is usually older than Node 20; get a current LTS release from
[nodejs.org](https://nodejs.org/en/download), with [nvm](https://github.com/nvm-sh/nvm) or from your distribution's Node repository, and check
`python3 --version` (3.12 or newer) and `node --version` (20 or newer) before you continue. The commands that start with `sudo` need an account that can use it;
without one, skip step 1's service user and the systemd steps and use "Try it in the foreground" below.

1. **Make a service user and folders:**

   ```bash
   sudo useradd --system --home /var/lib/embertide --create-home --shell /usr/sbin/nologin embertide
   sudo mkdir -p /opt/embertide && sudo chown "$USER" /opt/embertide
   git clone https://github.com/Ncbudd/ember-tide.git /opt/embertide/src
   ```

2. **Install the server** in its own virtual environment:

   ```bash
   python3.12 -m venv /opt/embertide/venv
   /opt/embertide/venv/bin/pip install /opt/embertide/src/core      # the same as `pip install ./core` from the project folder
   ```

3. **Build the web app** (this makes `web/dist`, which the server serves on the same port):

   ```bash
   cd /opt/embertide/src/web && npm ci && npm run build
   ```

4. **Set up the service.** Copy [embertide.service](embertide.service) to `/etc/systemd/system/` and adjust the time zone. Its paths match the
   steps above. The settings it uses:
   `ET_WEB_DIR` (the built web app), `ET_DATA_DIR` (the database folder, owned by the service user), `ET_PORT` and `ET_TZ`. Everything else is
   in [configuration.md](configuration.md); secrets can go in `/etc/embertide.env` (`chmod 600`, owner root).

   ```bash
   sudo chown -R embertide:embertide /var/lib/embertide
   sudo systemctl daemon-reload && sudo systemctl enable --now embertide
   journalctl -u embertide -f          # logs
   ```

   Check that it runs: `curl http://localhost:8790/healthz` should answer `{"ok":true,"version":"...","id":"..."}`.

   **Try it in the foreground (no service user, no systemd).** Handy for a first look. It uses the built-in simulator and a throwaway data folder,
   so no bed is needed; stop it with Ctrl+C:

   ```bash
   mkdir -p ~/embertide-data
   ET_WEB_DIR=/opt/embertide/src/web/dist ET_DATA_DIR=$HOME/embertide-data ET_PORT=8790 ET_TZ=Etc/UTC      /opt/embertide/venv/bin/python -m embertide
   ```

   Use your own time zone for `ET_TZ` (for example `Europe/London`). Without `ET_WEB_DIR` pointing at the built `web/dist`, the API runs but `/` has no web app.

5. Open `http://<machine>:8790` and continue with [B. First-run setup](#b-first-run-setup-in-the-app). For the real bed, the easy way is
   the maker's cloud: add `ET_BED=orion-cloud` and `ET_ORION_API_KEY=<your key>` to the service ([C, option 1](#c-point-the-bed-at-your-server)).
   The fully local way needs an MQTT broker on port 1883 (install Mosquitto from your distribution and use `deploy/mosquitto/` as the model)
   and the DNS step from [bed-cutover.md](bed-cutover.md).

Locked out of the owner account? Run `/opt/embertide/venv/bin/python -m embertide reset-password <username>` with the same `ET_DATA_DIR` set; it prints a one-time link.

To update: `git pull`, `pip install` the core again, rebuild the web app, `sudo systemctl restart embertide`. Back up `/var/lib/embertide`.

## Advanced: plain Docker and running from source

### Plain `docker run`

Without Compose, this runs only the app (the simulator):

```bash
docker run -d --name ember-tide --restart unless-stopped \
  -p 8790:8790 \
  -v ember-tide-data:/data \
  -e TZ=Europe/London \
  ghcr.io/ncbudd/ember-tide:latest
```

For a real bed against a broker you already run, add `-e ET_BED=orion-mqtt -e ET_MQTT_URL=mqtt://192.168.1.10:1883`. A named volume (above) or a folder
(`-v /srv/ember-tide:/data`) both work; the database is `/data/embertide.db`. The container runs as uid and gid 1000, not root. A fresh named volume just works; a folder that an older
(root) image already wrote to needs a one-time `chown -R 1000:1000 /srv/ember-tide` (see [troubleshooting](troubleshooting.md#the-data-folder-cannot-be-written-synology-unraid-nas)). The image listens on `0.0.0.0:8790`, keeps its data in `/data`, and has a health check on `/healthz`.

### Build the image yourself

If you would rather not use the published image (or you changed the code), build it from the project folder and use it in place of the published one:

```bash
docker build -t ember-tide .
```

In `docker-compose.yml`, comment out the `image:` line and uncomment `build: .` (or replace `image:` with `image: ember-tide`), then run `docker compose up -d --build`.
On the pasted-in platforms, use `image: ember-tide` and the full project path as in the table in [A](#a-the-compose-file-and-the-broker).

### From source (for development)

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
ET_AUTH=off ET_TZ=Europe/London ET_DATA_DIR=./data-dev uv run python -m embertide
```

Open `http://localhost:8790`. While working on the interface, run `npm run dev` in `web/` instead; it proxies the API to port 8790 and reloads on save.
`ET_AUTH=off` removes sign-in and is for local testing only. Set `ET_WEB_DIR` if you keep the built web app somewhere else. On Windows, use
`.venv\Scripts\python.exe -m embertide` and set variables with `$env:NAME = "value"`. Type checks and build: `cd web && npx tsc --noEmit -p . && npm run build`.

---

## Glossary

- **Hub** (or bed hub): the control unit of the Orion topper. It is the thing that connects to your network.
- **Server**: the Ember & Tide program on your always-on computer. The app and Studio are its web pages.
- **MQTT**: a very small message system that devices use to talk. The bed sends its readings and receives commands this way.
- **Broker**: the post office for MQTT messages. The hub and Ember & Tide both talk to it. Ours is Mosquitto, started by the Compose file.
- **DNS override** (or local DNS record): a line in your router or DNS server that makes a name, here the maker's server name, point at a computer in your home instead.
- **Compose**: a text file that tells Docker which programs to start and how they are set up.
- **Container, image**: a container is a program running in Docker; an image is the packaged program it starts from.
- **Appdata, volume**: the folder on the server where a container keeps what it must not lose. For us it is the `data` folder (the database).
- **Reservation (DHCP reservation)**: a router setting that always gives the same address to the same device.

---

Next: [connect the bed](bed-cutover.md), [build the Android app](android.md), [every setting](configuration.md), [when something is wrong](troubleshooting.md).
