# Troubleshooting

Start with the server log (`docker compose logs -f embertide`) and Studio, **Bed hub** and **Overview**; both say in plain
words what the bed and each sensor are doing.

## The bed hub won't connect

1. **Does the name resolve to your broker?** From another computer: `nslookup dev.orionbed.com` must print your broker's address.
   If it prints a public address, the DNS record isn't being used. Check the record, and that your devices (and the hub) use your
   router or DNS server for DNS.
2. **Did you power-cycle the bed after adding the record?** The hub only looks up the name when it starts.
3. **Is the broker listening for the bed?** `docker compose ps` shows `mosquitto` healthy; port 1883 must be reachable from the bed's
   network (check a firewall between the two). `docker compose logs mosquitto` should show a new client when the bed starts.
4. **Does the bed reconnect over and over?** Use Mosquitto **2.0.20**. 2.0.21 changed keep-alive handling in a way the bed's firmware
   doesn't like.
5. **Is a rule refusing the hub's messages?** The access list allows only `orion/+/in` and `orion/+/out`. If you edited it, or the
   broker log shows denied publishes, restore the two `topic readwrite` lines from `deploy/mosquitto/acl`.
6. **Server side:** `ET_BED=orion-mqtt` and `ET_MQTT_URL` pointing at the broker as seen from the server (`mqtt://mosquitto:1883`
   inside the same Compose project, not `localhost`). If you have several beds, set `ET_ORION_SERIAL`.
7. Watch for messages directly: `docker compose exec mosquitto mosquitto_sub -t 'orion/+/out' -C 3`.

## Platform pitfalls

### Port 1883 is already in use

The bed can only talk to port 1883, so the machine that answers the bed's DNS name must own it. If `docker compose up` says
"port is already allocated" or "address already in use" for 1883, another program holds it: often another Mosquitto
(a Home Assistant add-on on the same machine, an Unraid or Synology container, a package install). Either stop that one, give *it* another host port,
or run Ember & Tide on a different machine and point the DNS record there. Do not change the left side of `"1883:1883"` for the bed's broker.
The web port is different: if 8790 is taken, change the left number (`"8791:8790"`) and use that port in the address.

### The data folder cannot be written (Synology, Unraid, NAS)

Symptoms: the app restarts over and over, and `docker compose logs embertide` mentions "unable to open database file" or "permission denied".
The container runs as an unprivileged user (uid and gid 1000), so the most common cause is ownership: a data folder created by root (by an older image, or by hand) cannot be written.
The fix, once:

- **Any system:** in the project folder, run `chown -R 1000:1000 ./data` (over SSH or a terminal). Or tell the container to run as the folder's owner by adding `user: "<uid>:<gid>"` under the `embertide:` service (find the numbers with `ls -ln ./data`) and removing the `data-init` service.
- **Unraid:** use `user: "99:100"` (nobody:users, Unraid's own owner for shares), or from the terminal run `chown -R 1000:1000 /mnt/user/appdata/embertide/data`.
- **Synology:** use the numbers of the user that owns the shared folder (often `1026:100`; `id <username>` over SSH shows them) in `user:`, or `chown` the folder to `1000:1000`.
- Or keep the old behaviour by adding `user: "0:0"` to the service in the Compose file.

Other causes are the folder itself: the `data` folder was not created, the path in the Compose file is wrong
(a relative `./data` in a stack tool that keeps the file elsewhere; use the full path), or the share is read-only or has a restrictive ACL.
On Unraid, use `/mnt/user/appdata/embertide/data` and check the share is not read-only. On Synology, create the folder inside a shared folder such as
`docker`, and check Control Panel, Shared Folder, Permissions. Create the `data` folder yourself before starting, and keep it off network mounts that do not support file locking.

### Docker will not start in a Proxmox LXC

Docker inside an LXC needs the container feature **Nesting** (Container, Options, Features; add **keyctl** if offered). Without it you see
permission errors when containers start. Turn it on, restart the container, and start the stack again. If it still fails, use a small VM instead.

### Other devices cannot reach the app on Windows

Windows blocks incoming connections by default. Allow Docker Desktop on private networks when asked, or open TCP 8790 and 1883 for your Private network
(run these in a PowerShell opened as administrator):

```powershell
New-NetFirewallRule -DisplayName "Ember & Tide web" -Direction Inbound -Protocol TCP -LocalPort 8790 -Profile Private -Action Allow
New-NetFirewallRule -DisplayName "Ember & Tide broker" -Direction Inbound -Protocol TCP -LocalPort 1883 -Profile Private -Action Allow
```

 Make sure the network profile is Private, not Public, and that the computer does not sleep.

## Both sides say "unknown" (or someone is shown as not in bed while lying there)

- The bed isn't reporting: see above. The status line on Tonight says "The bed isn't answering right now".
- A bed sensor whose internal clock stops changing is treated as **stale**, not as a person or an empty bed. After a hub restart it
  recovers within a minute or two.
- Occupancy has to hold for three seconds before it counts; a quick sit-and-stand won't start a night.
- Check Studio, Nights explorer, "In bed: bed's own sensor" against "final answer" to see which source said what.

## One person shows up on both sides ("echo")

The sensor strip often picks one sleeper up on both pads with identical readings. Ember & Tide ignores the echo side by itself.
If it gets it wrong, tell it who is where: add a **bed scale** or **presence radar** in Studio, Devices & calibration (they are
the most reliable way to tell two people apart), and use "Fix this night" on the Sleep screen for past nights.

## Push notifications don't arrive

- **Studio, Notifications** lists the phones that are signed up and a warning for anyone who has none ("Alex's notifications aren't
  reaching any phone"). Turn notifications on per phone: More, Settings, Notifications.
- **Android with Google push**: needs your own `google-services.json` in the app build and `fcm-service-account.json` on the server
  ([Android](android.md)). Studio shows "On (Firebase project ...)" when the server side is ready. Battery optimisation on some phones
  stops background delivery; allow the app to run in the background.
- **Android without Google (ntfy/UnifiedPush)**: install the ntfy app, make sure Studio, Notifications has the same ntfy server, and
  that a self-hosted ntfy allows writes to `up*` topics.
- **iPhone / browsers (Web Push)**: needs HTTPS, iOS 16.4+, and the app opened from the Home Screen icon. Set `ET_PUBLIC_URL`. Send a test
  from Studio, Notifications.
- **Buttons in notifications do nothing**: the phone has to reach the server at `ET_PUBLIC_URL`. At home the LAN address works; away
  from home you need a reachable HTTPS address. Each button works once and expires after 12 hours.
- **Quiet hours** hold non-urgent messages until morning, and each kind has a daily cap by design.
- Test through the same path: Studio, Notifications, **Test**.

## Home Assistant entities are missing

1. **Studio, Integrations, Home Assistant** should say connected. If not, the broker address is wrong or the login is rejected
   (`ET_HA_MQTT_URL=mqtt://user:password@host:1883`).
2. Home Assistant needs the **MQTT integration** set up against the same broker, and the discovery prefix must match (`homeassistant`
   by default, `ET_HA_DISCOVERY_PREFIX`).
3. Press **Publish again** on that Studio page; entities show within seconds. Entity ids start with `climate.embertide_<name>`, the names
   are the people's names.
4. Changed a name? Home Assistant keeps the old entity id; remove and republish from the same Studio page.

## Health Connect data arrives late or not at all

- Watches write a night's heart rate, SpO2 and sleep into Health Connect **hours after waking**, especially Samsung Health. Ember & Tide
  re-reads the last 48 hours on every sync, so a night fills in during the day. Open the app once to force a sync.
- Check the permissions: Health Connect, App permissions, Ember & Tide, and "Read in the background" (Allow).
- Settings, Watch or ring shows each night's coverage per device and what is missing in plain words.
- `adb logcat -s EmberHealth` shows each sync's window and counts per kind (no values).
- Two apps writing the same reading is handled: duplicates are dropped when the original app has a reading within 30 seconds.

## I can't create the owner account

First-time setup only works from your home network, on the server's local address (`http://192.168.1.20:8790`), never through a proxy
or tunnel. Use the local address once, then sign in from anywhere. Locked out later:
`docker exec -it ember-tide python -m embertide reset-password <username>`.

## The phone app says "Lost the connection"

The phone can't reach the server: wrong network, VPN or tunnel down, or the server is off. Away from home, the app needs your
HTTPS address, entered as the server address at sign-in.

## Times are an hour out or the night starts at the wrong time

`TZ` in the Compose file must be your real time zone (for example `America/New_York`). After changing it, run `docker compose up -d` again. (`ET_TZ` also works and wins if both are set.)

## Still stuck

Open an issue with the version (`/healthz` shows it), what you expected, what happened, and the relevant log lines. Please remove
passwords, tokens and serial numbers first.
