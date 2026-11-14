# Testing Ember & Tide (beta)

Thank you for trying it. This page is everything a tester needs: what to expect, how to stay safe, what to try and how to tell us what happened.

## What this is

Ember & Tide is a self-hosted controller for the Orion Sleep dual-zone water-cooled mattress topper. A small server at home gives each
sleeper their own temperature rhythm through the night, with a phone app, a watch app and Home Assistant support. Your sleep data stays on your
own server. **It is an unofficial community project, not affiliated with or endorsed by Orion Sleep or its maker.** "Orion" only names the hardware
it works with. It is early software (0.x), so expect rough edges.

## Safety first

This software controls a device that heats and cools a bed.

- **Keep the built-in limits on** (the everyday temperature floor and ceiling, and the maximum change per step). Do not look for ways around them.
- **If anything feels wrong** (too hot, too cold, odd noises, a side that will not stop), turn that side off at the hub or the bedside dial, or in
  Orion's own app, then stop the Ember & Tide server. The bed's own controls always win.
- **It is not a medical device.** Sleep stages, scores and suggestions are estimates for comfort, not advice about your health.
- Try anything new on a night when you can easily switch it off.

## Which way to connect (pick one)

Not sure yet? The [public demo](https://embertide-demo.homepush.dev) runs in your browser with a simulated bed and two made-up
sleepers. Nothing to install, and it resets every hour.

1. **Simulator first, no bed needed.** This is the default. Install, create your account, and look around in **Studio, Simulator**. Do this before anything else:
   [Try it without a bed](docs/install.md#try-it-without-a-bed).
2. **Recommended for real use: `orion-cloud`.** Set `ET_BED=orion-cloud` and `ET_ORION_API_KEY` to a personal API key from the maker's web app.
   No network changes, and the maker's own app keeps working next to it. It polls about every 10 seconds, so it is a little slower. See
   [configuration](docs/configuration.md).
3. **Advanced: local control (`orion-mqtt`).** Faster and works without the internet, but it needs a local DNS override so the bed's hub talks to your own
   broker. That is outside what the maker supports, and while it is in place the maker's cloud features and firmware updates cannot reach the bed. Read
   [Bed cutover](docs/bed-cutover.md) first. **To undo it:** remove the DNS record (and any internet block for the hub), power-cycle the bed, and
   the official app sees it again ([rolling back](docs/bed-cutover.md#rolling-back-to-the-makers-cloud)). This project blocks firmware commands on
   purpose; see [updating the firmware on your terms](docs/bed-cutover.md#updating-the-firmware-on-your-terms).

## What to test this round

Tick off what you can, and tell us about anything that confused you, not only things that broke.

- [ ] **Install** with the [install guide](docs/install.md) for your platform. Was any step unclear or missing?
- [ ] **First-run setup:** the owner account and the short setup questions. Did the defaults make sense?
- [ ] **Rhythm editor:** change bedtime, wake time and a phase temperature; check the plan shown for tonight.
- [ ] **A normal night:** get in bed, sleep, get up. Did the bed follow the plan? Anything odd when you got up in the night?
- [ ] **Morning summary:** is the story of the night readable and roughly right?
- [ ] **Notifications:** turn them on for your phone, check they arrive, and check that quiet hours behave. The Android app works out of the box through the push relay (no Firebase file needed; Studio, Notifications shows the path in use). iPhone uses Web Push and needs HTTPS.
- [ ] **Leak sensors (optional):** wet a connected water leak sensor (or simulate it); check both sides turn off immediately, stay off after drying until cleared on Tonight or Studio, and send an urgent notification to the household.
- [ ] **Android app:** sideload the APK from the release ([android.md](docs/android.md)), then type the same address you use in the browser (for example `192.168.1.20:8790`); try the widget, the Hot flash tile, and the Wear OS app if you have a watch.
- [ ] **iPhone web app:** open it in Safari over plain `http://` at home; notifications need HTTPS ([simplest way](docs/install.md#d-https-phones-away-from-home-and-iphone-notifications)), iOS 16.4 or newer.
- [ ] **AI model page (optional):** Studio, Insights, if you have a model to point it at ([ai-models.md](docs/ai-models.md)).
- [ ] **MCP (optional):** connect an AI app to ask about your sleep ([mcp.md](docs/mcp.md)).

## Known limits

- The Android app comes from the GitHub release page, not the Play Store. Each release lists the signing fingerprint of its APKs so you can check it, and an update only installs over an app signed with the same key.
- On iPhone you use the web app on the Home Screen. A native app is planned.
- Screens and notification wording are English only for now.
- One household per server (two sleepers, plus viewers).
- Firmware updates for the bed are blocked by this project on purpose.

## How to report a problem

- Open an issue with the [bug form](https://github.com/ncbudd/ember-tide/issues/new/choose) (or the feature form for ideas). Please include:
  the **version** (Studio → Help shows it, or open `http://<your-server>:8790/healthz`), how you run it (Docker or bare metal), the **driver** (`sim`, `orion-cloud` or `orion-mqtt`), your phone, watch and app
  version, what happened and what you expected, and the relevant **log lines**.
- **Remove secrets before pasting anything:** API keys, passwords, tokens, bed serial numbers, public addresses and domains.
- **Security problems go in private**, not in a public issue: see [SECURITY.md](SECURITY.md).
- Your data stays on your server and nothing is sent to us. Safe to share: the version, the driver, log lines with secrets removed, and screenshots taken with the simulator or with names blurred. Do not share your database file or your data folder.
- [Troubleshooting](docs/troubleshooting.md) covers the common problems, and there is a form for hardware that is not supported yet.

## Updating and backing up

Nothing updates itself. [Updating and backups](docs/install.md#e-updating-and-backups) shows how to pull a new version and what to copy (the `data` folder). Take a backup before every update.

## Removing it completely

1. Docker: `docker compose down`, then delete the project folder, including its `data` folder. Bare metal: `sudo systemctl disable --now embertide`, then delete `/opt/embertide` and `/var/lib/embertide` (or the folders you used).
2. If you used local control, remove the DNS record and power-cycle the bed so it goes back to the maker's cloud.
3. Remove the Android app, the Wear OS app and the iPhone Home Screen icon. If you connected Home Assistant, remove the entities from Studio first.
4. Revoke the personal API key in the maker's web app if you made one for this.

## Licence in plain words

Free for personal and household use, and for noncommercial organisations, under the [PolyForm Noncommercial 1.0.0](LICENSE) licence. No commercial use without written permission.
The source is available for you to read, change and share under those terms. It is *source-available*, not open source.
