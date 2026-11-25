---
name: unifi-troubleshooting
description: How to troubleshoot a UniFi network from what the Integration API exposes — slow Wi-Fi (retries, channels, uplink), an offline device (uplink, PoE), a client that cannot connect, and firmware hygiene. Use when diagnosing a Wi-Fi or LAN complaint or planning firmware updates.
---

# Troubleshooting a UniFi network

## 1. Know what you can see

The console's Integration API gives, per site: devices (state, model, firmware, whether an update is
available, uplink device), per-device statistics (uptime, CPU, memory, load, uplink rates, per-radio
transmit retries), ports with PoE state, clients (wired or wireless, the device they hang on, guest
authorisation), networks (VLAN) and Wi-Fi broadcasts. It gives **no alarms or events, no per-client
signal strength or SSID, and no target firmware version**, and it edits no SSID, VLAN or channel. When the
evidence you need is one of those, say it is not exposed and name the console screen to look at.

Statistics are a snapshot. One reading is a lead, not a trend: say when it was taken and what a second
reading, an hour later or at the time of the complaint, would show.

## 2. Slow Wi-Fi

1. Locate the access point of the room (names usually carry the room), and the clients on it.
2. Read **both radios**. The signal that matters is the transmit retry rate (`tx_retries_pct`): below
   about 10 % is healthy, 10 to 20 % is worth watching, above 20 % on a band means many frames are sent
   twice, which is what users feel as slowness. These are rules of thumb: say them as such.
3. A high retry rate on one band only, with healthy CPU, memory and uplink, points at **radio interference
   or a crowded channel**: compare the channel and width of that radio with the neighbouring access points.
   Two neighbours on the same channel, or a 40 MHz width on 2.4 GHz, are the usual causes.
4. A high retry rate on both bands, or poor performance on wired clients too, points past the radio: the
   uplink (rates close to the link's capacity, a port negotiated at 100 Mbit/s instead of 1 Gbit/s), or the
   switch.
5. High CPU or memory with a long uptime, or a firmware older than its neighbours', points at the device:
   restart it **only then**, and only one at a time.
6. Propose a channel or width change as a **change plan** (the API cannot make it): current value, proposed
   value, how to check the retry rate afterwards, how to revert.

## 3. A device is offline

- `OFFLINE`, `CONNECTION_INTERRUPTED` and `ISOLATED` need a cause. `UPDATING`, `GETTING_READY` and
  `ADOPTING` are in progress: wait, do not restart. `PENDING_ADOPTION` is a device nobody adopted.
- Follow the **uplink**: which device and port it hangs on. If the switch is online and the port is down or
  not powering the device, the cause is the cable, the port or the PoE budget, not the device.
- Look at what else shares the switch: several devices offline behind the same uplink point at the
  uplink, not at each of them.
- Power-cycle a PoE port only for a device powered by that port and not answering, name what loses power, and
  never a port that carries an uplink to another switch or a gateway. A restart cannot reach an offline device.

## 4. A client cannot connect

- Is it in the client list at all (name, MAC, IP)? A client never seen is a client that never reached the
  network: the cause is before the console (Wi-Fi password, the wrong SSID, range, the wrong VLAN's DHCP).
- Wired or wireless, and which device and port it hangs on. A wired client on a port that is down is a cable.
- On a guest network, a client whose access is `GUEST` and not authorised sits behind the hotspot's portal:
  authorising it is a decision about access (a person asks and is accountable), with a time limit, never
  open-ended. Vouchers are the alternative for several people.
- Never ask for, and never write down, a Wi-Fi passphrase or a voucher code: the server withholds both.

## 5. Firmware hygiene

- Drift is several firmware versions of one model on a site, or devices with an update available. Offline
  devices cannot update: list them apart, they are the first thing to fix.
- Update in an order that never takes a whole area down: one device first, wait for it to be back and
  healthy (state `ONLINE`, retry rate unchanged), then the others, a few at a time; the gateway and the core
  switch last, in a maintenance window, because every client behind them goes with them.
- The API tells only that an update exists: read the release notes on the console before approving, and keep
  to the vendor's recommended release channel.
- Applying firmware is not a tool of this plugin: it goes in the change plan.

## 6. What a good report holds

Findings with the device, the field, the value and the time it was read; the likely cause and the
alternatives; proposals with their risk (who loses connection, for how long); a change plan for what the
API cannot do; and what the API could not tell you.
