# Running an Orion topper fully locally

The Orion topper talks MQTT to `dev.orionbed.com` on port 1883. If your network answers that name with the
address of your own broker, the bed connects there instead. Ember & Tide then reads telemetry from
`orion/<serial>/out` and sends commands to `orion/<serial>/in`. No cloud or account is involved.

## What you need
- An MQTT broker reachable by the bed on port **1883** (Mosquitto works well). The bed connects
  **anonymously**, so give it its own listener and keep that listener on your LAN only.
- A router (or local DNS server such as Pi-hole, AdGuard Home or UniFi) where you can add a DNS record.

## Steps
1. Run a broker. A minimal Mosquitto config for the bed listener:
   ```conf
   listener 1883 0.0.0.0
   allow_anonymous true
   acl_file /mosquitto/config/acl
   ```
   with an ACL that only allows `orion/+/in` and `orion/+/out`.
2. Add a DNS record: `dev.orionbed.com` → your broker's LAN IP. Give the bed a DHCP reservation.
3. Power-cycle the bed. Within a minute you should see `orion/<serial>/out` messages about once per second.
4. Start Ember & Tide with `ET_BED=orion-mqtt` and `ET_MQTT_URL=mqtt://<broker-ip>:1883`.
5. Optional, recommended: block the bed's internet access in your router so nothing can change its firmware
   without you.

## Firmware updates (on your terms)
While local, the official Orion app can't see the bed. To update firmware deliberately:
1. Remove the DNS record (and any internet block for the bed).
2. Power-cycle the bed. It reconnects to Orion's cloud.
3. Update from the official Orion app.
4. Put the DNS record back and power-cycle the bed again.

Ember & Tide shows the bed's firmware version in Studio. After an update, check that it still reports
normally before going back to sleep on it.

## Known quirks (handled for you)
- One sleeper often shows up on **both** sensors with identical readings ("echo"). The echo side is ignored.
- Empty sides report "occupied" for about half a second at random. Occupancy must hold for 3 seconds to count.
- A sensor whose internal clock stops changing is treated as stale (unknown), not as a person.

Protocol credit: tdickman's local integration and firmware notes shared on r/Orion_Sleep.
