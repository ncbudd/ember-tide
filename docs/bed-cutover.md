# Connecting the bed locally (the cutover)

The Orion topper's control unit (the "hub") talks MQTT to the maker's server, `dev.orionbed.com`, on port 1883.
If your network answers that name with the address of **your own broker**, the hub connects there instead. Ember & Tide
then reads telemetry from `orion/<serial>/out` and sends commands to `orion/<serial>/in`. No cloud account is involved,
updates arrive about once a second, and it keeps working when the internet is down.

The trade: while the hub talks to your broker, the maker's app can't see the bed. You can switch back at any time
(see [Rolling back](#rolling-back-to-the-makers-cloud)).

This is unofficial and not supported by the maker. Read the [disclaimer](../README.md#status-and-disclaimer) first.

## Before you start

- Ember & Tide and a broker are running (the [Compose file](install.md#a-the-compose-file-and-the-broker) does both), and the machine
  has a fixed address, for example `192.168.1.20`. Set a DHCP reservation for it.
- Give the **bed hub** a DHCP reservation too (it makes it easy to find in your router and to block its internet later).
- `ET_BED=orion-mqtt` and `ET_MQTT_URL=mqtt://mosquitto:1883` (or your broker's address) are set on the server.
- The broker accepts anonymous connections on port 1883 for `orion/+/in` and `orion/+/out`. The example in
  `deploy/mosquitto/` does. Use Mosquitto 2.0.20 (see [install](install.md#a-the-compose-file-and-the-broker)).

## 1. Add the DNS record

Create a local DNS record: `dev.orionbed.com` points to your broker's address (`192.168.1.20` in these examples).
Use whichever of these your network uses.

**UniFi (Network application)**: Settings, Routing (or Security), DNS / "Policy Table" or "Local DNS Records" depending on the
version. Add a record of type A, hostname `dev.orionbed.com`, IP `192.168.1.20`, and save.

**Pi-hole**: Settings, Local DNS Records (or Local DNS, DNS Records). Domain `dev.orionbed.com`, IP `192.168.1.20`, Add.

**AdGuard Home**: Filters, DNS rewrites, Add DNS rewrite: domain `dev.orionbed.com`, answer `192.168.1.20`.

**dnsmasq / OpenWrt / OPNsense (Unbound)**:

```conf
# dnsmasq
address=/dev.orionbed.com/192.168.1.20
# Unbound
local-data: "dev.orionbed.com. A 192.168.1.20"
```

On OpenWrt, add the dnsmasq line under Network, DHCP and DNS, "Addresses".

Check that the record works from another computer on the network:

```bash
nslookup dev.orionbed.com           # should print 192.168.1.20, not a public address
```

The record only works if the hub asks your DNS server. Hubs normally use the DNS server your router hands out by DHCP. If your
router is set to give out a public resolver (or the hub is on a separate network that uses one), make sure that network's DNS
is your server, or redirect port 53 from the hub to it in your firewall.

## 2. Power-cycle the bed

Unplug the control unit for ten seconds and plug it in again. The hub looks up `dev.orionbed.com` when it starts.

## 3. Check that it connected

Within a minute:

- Studio, **Bed hub** (and Overview) show the bed online, with its status, firmware and Wi-Fi signal.
- The broker sees it:

  ```bash
  docker compose logs mosquitto | tail            # "New client connected ... as <the hub>"
  docker compose exec mosquitto mosquitto_sub -t 'orion/+/out' -C 3
  ```

  You should see three JSON messages with the bed's serial number in the topic, roughly one per second.
- On the phone, Tonight shows the pad temperature and, with someone lying on it, heart rate and breathing rate.

Serial number: Ember & Tide picks it up from the first bed that reports. If you have more than one bed on the network,
set `ET_ORION_SERIAL` to the one it should control.

## 4. Optional but recommended: block the hub's internet

With the DNS record in place the hub already talks to your broker. Blocking its internet access (a firewall rule for the
hub's address that denies traffic to the internet, but allows the local network) makes sure nothing can change its firmware
without you. Keep DNS and the connection to your broker (LAN) allowed.

If the hub shows offline after you add the rule, check that the rule only blocks "to internet" and that your broker is on
the same network or that traffic between the two networks is allowed on port 1883.

## Rolling back to the maker's cloud

1. Remove the DNS record (and any internet block for the hub).
2. Power-cycle the bed. It reconnects to the maker's servers and the official app sees it again.
3. Optionally set `ET_BED=orion-cloud` and an API key if you still want Ember & Tide to control it; the cloud driver
   polls about every 10 seconds.

## Updating the firmware (on your terms)

Ember & Tide never sends firmware, Wi-Fi or low-level pump and valve commands; they are refused on purpose, so nothing
here can brick the unit or change its software. To update deliberately:

1. Remove the DNS record and the internet block.
2. Power-cycle the bed so it reconnects to the maker's cloud.
3. Update from the official app.
4. Put the DNS record back and power-cycle the bed again.
5. Check Studio, Bed hub: it should report normally. Firmware changes have changed the data format before, so watch the first
   night.

Ember & Tide has been verified on firmware **2.6.2**; commands the project has not seen working on real units are not offered.

## Known quirks (handled for you)

- One sleeper often shows up on **both** sensors with identical readings ("echo"). The echo side is ignored.
- An empty side reports "occupied" for about half a second at random. Occupancy must hold for 3 seconds to count.
- A sensor whose internal clock stops changing is treated as stale (unknown), not as a person.
- Some messages arrive with a damaged value; the two fields known to be affected are repaired, nothing else.
- After a hub restart the unit forgets its temperatures; Ember & Tide sends the current targets again as soon as it reports.

Protocol credit: tdickman's local integration and firmware notes shared by the community.
