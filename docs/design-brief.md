# netmon — design handoff brief

**Status:** written 2026-07-27, after the v0.3 outage acceptance test.
**Purpose:** ground a design review in what netmon can actually show, measured rather than
assumed. Creative direction is supplied separately; this document deliberately contains none.

Every number below was read from the live database and the running instance, not from the
roadmap. Where a capability is aspirational it is marked as such.

---

## 1. What netmon is

A home network monitor for a single household, running as a background service on one Windows
PC. It exists to answer four things, in the owner's priority order:

1. **Troubleshoot connection problems** — the primary goal. When the internet misbehaves, say
   what broke and when.
2. **Monitor bandwidth.**
3. **Throttle specific devices** — explicitly future work, gated on a hardware purchase.
4. **Serve as a general network dashboard.**

Goal 1 is the one the product is actually good at today. Goals 2 and 3 are constrained by
physics and hardware in ways design cannot route around, described below.

---

## 2. The constraint that shapes everything

netmon runs as an **ordinary host on a switched LAN**, behind a consumer eero 7 mesh router.
A switch only forwards frames addressed to the host, so netmon **cannot passively observe other
devices' traffic**. The eero has no local management API, no SSH, no SNMP, and no UPnP byte
counters — this was confirmed by direct probing, not assumed (see `docs/eero-capabilities.md`).

| Capability | Reality | Why |
|---|---|---|
| Device inventory & presence | ✅ Works | ARP + ICMP sweep |
| Connection troubleshooting | ✅ Works — the strongest area | Latency, DNS, HTTP, outage detection, traceroute |
| **This PC's** bandwidth | ✅ Works | Per-NIC and per-process counters |
| **Total WAN** bandwidth | ❌ **Not available** | The eero's UPnP byte-counter service is an unimplemented stub |
| **Per-device** bandwidth | ⚠️ Gated | eero's cloud API offers it free, but **not to Amazon-SSO accounts**. Needs a separate eero admin account that does not exist yet |
| Throttling | ❌ Blocked | Needs an enforcement point the household does not own |

**Design consequence:** there is no whole-network throughput number, and there may never be one.
A dashboard organised around "here is your household's bandwidth" cannot be built on this
hardware. A dashboard organised around *connection quality* can.

---

## 3. The current dashboard is a placeholder, not a starting point

`src/netmon/web/index.html` is **84 lines**, one file, no build step, no dependencies, no
framework. Vanilla JS, a hand-drawn `<canvas>` line chart, polling four endpoints every 10
seconds. It renders four stacked sections: a headline verdict, a latency chart, an event table,
and a device table.

It was written to prove the API works and has been **frozen since v0.3** — deliberately
untouched through three subsequent releases so that design decisions are made once, properly,
rather than accreting. **Please treat it as disposable.** It carries no design intent worth
preserving and should not anchor anything.

---

## 4. The complete API surface

Nine endpoints. This is everything; there is no other data source.

| Endpoint | Parameters | Returns |
|---|---|---|
| `GET /status` | — | Current verdict, per-target hysteresis state, WAN status |
| `GET /devices` | — | All known devices with IPs, online flag |
| `GET /devices/{id}` | — | One device |
| `GET /metrics` | `kind, target, source, start, end, limit` | Time-series samples |
| `GET /events` | `limit, kind, severity, start, end` | Timeline: outages, device joins/departures |
| `GET /events/{id}` | — | One event, including traceroute payload |
| `GET /alerts` | `limit, severity, start, end` | Alert history |
| `GET /health` | — | Liveness only, unauthenticated |
| `GET /` | — | The dashboard |

Auth is a bearer token, with loopback exempt by default — so a local dashboard needs no login
screen and none should be designed. Everything is read-only: **there is currently no write path
in the API at all.** Any interaction that changes state (naming a device, creating an alert
rule, triggering a diagnostic) requires new backend work, which is fine — but it should be
called out as such rather than assumed.

---

## 5. What data actually exists

Measured from the live database. `n` is real sample count.

| Metric kind | Unit | Targets | Reliable? |
|---|---|---|---|
| `latency_rtt_ms` | ms | 3 (gateway, 1.1.1.1, 8.8.8.8) | ✅ Yes — the backbone of the product |
| `latency_jitter_ms` | ms | 3 | ✅ Yes |
| `latency_loss_percent` | percent | 3 | ✅ Yes |
| `dns_resolution_ms` | ms | 4 resolver/name pairs | ✅ Yes |
| `http_ttfb_ms` | ms | 1 | ✅ Yes |
| `http_status_code` | code | 1 | ✅ Yes |
| `host_nic_bytes_sent_rate` / `_recv_rate` | bytes/sec | 5 adapters | ✅ Yes — **this PC only** |
| `wan_uptime_seconds` | seconds | 1 | ⚠️ **Unreliable — see §6** |
| `wan_connected` | boolean | 1 | ⚠️ **Unreliable — see §6** |

Event kinds present: `device_join`, `device_depart`, `outage_start`, `outage_end`,
`wan_reconnect`.

---

## 6. What is missing, unreliable, or empty

This section matters more than §5. Designing against the optimistic version of this product
would produce something that cannot be built.

**Devices have a category, but not an identity.** The device table holds 18 rows, of which
**16 are real** — `00:00:00:00:00:00` and `ff:ff:ff:ff:ff:ff` are the unspecified and broadcast
addresses, ARP artefacts that discovery does not currently filter out. Of the real devices:

- **16 of 18 resolve to a vendor.** The full IEEE registry is now bundled, so this is as good
  as it will get. One device uses a privacy-randomised MAC and will never resolve; no registry
  can fix that.
- **6 of 18 have a hostname.** Reverse DNS and NetBIOS work; mDNS does not run on Windows, so
  Apple and IoT devices resolve to nothing.
- **0 have a label.** A `label` column exists in the schema but nothing reads or writes it —
  there is no way for a person to name a device.

So the device list reads like this:

```
eero inc.                    34:bc:5e:b7:44:ed    -
Amazon Technologies Inc.     6c:99:9d:70:fe:b5    -
Amazon Technologies Inc.     90:a8:22:fa:8d:bb    -
Roku, Inc                    60:92:c8:49:b1:d0    -
Roku, Inc                    d4:be:dc:eb:2b:c2    -
CLOUD NETWORK TECHNOLOGY…    ec:91:61:96:b1:97    BRWEC916196B197
(unknown)                    c2:2a:4a:43:fc:29    -
```

That is the actual shape of the problem, and it is more interesting than "no data". A vendor
name tells you *what kind of thing* something is; it does not tell you *which* thing. Two
Rokus and two Amazon devices are mutually indistinguishable, the household's Brother printer
appears as its ODM rather than its brand, and the one device someone actually cares about
identifying — a phone — is the one that resolves to nothing at all. Turning this list into
something a person recognises is the single biggest open design problem, and it cannot be
solved with better data collection.

**The router's WAN signal cannot be trusted.** During a real two-minute WAN disconnection the
eero reported `Connected` throughout and its uptime counter advanced 1,547,273 → 1,547,443 —
exactly 170 seconds across 170 seconds of wall clock. It counted straight through a severed
cable. Separately, the probe recorded **zero samples for the entire outage**. netmon now treats
this signal as evidence only when it *incriminates*, never as evidence of health, and the API
distinguishes `absent` / `stale` / `available_no_positive_evidence` / `positive` so a consumer
can tell "no corroboration available" from "corroborated healthy". **Those are different states
and should not render identically.**

**There is no history.** Retention is configured for 7 days raw and a year of rollups, but
netmon has never run continuously for more than about 30 minutes. Every chart must be designed
to be honest at 30 minutes of data *and* at 30 days. There is no dense historical dataset to
design against, and mockups populated with a year of smooth data would be fiction.

**Alerts are empty.** The rule engine, three notifier backends, and `/alerts` all work, but the
default rule list is `[]` and no config file exists on the machine. `/alerts` returns nothing
and will keep returning nothing until rules are authored — and rules can only be written by
hand-editing a TOML file. There is no UI path to create one.

**Speedtest is opt-in and disabled by default,** so there is no throughput-over-time data.

---

## 7. A real outage, as netmon recorded it

Concrete material to design against. The WAN cable was physically pulled for ~2 minutes on
2026-07-27. All times UTC.

```
02:58:09  1.1.1.1 hits 80% loss.  Gateway still perfectly healthy.
02:58:38  Total loss. The gateway ALSO stops answering — 100% loss.
02:59:44  Outage opens, backdated to 02:58:09.  Detection lag: 95 seconds.
03:00:19  Traffic restored.
03:00:42  Outage closes, backdated to 03:00:19.  Duration: ~130s.
```

Three things a designer should take from this:

1. **Detection lags reality by ~95 seconds** by design — hysteresis is what stops every
   transient blip becoming an alert. But the UI will regularly be showing "healthy" while the
   user is already staring at a dead browser tab. That gap needs a deliberate answer.
2. **Onset is gradual, not binary.** There was a 29-second window where the internet was
   degraded but the LAN was fine. Health is not a boolean, though `/status` currently reduces
   it to a single verdict string.
3. **Pulling the WAN cable also killed the LAN gateway** for 66 seconds. This is normal eero
   behaviour. It means the honest classification is frequently ambiguous — netmon can now say
   "ISP outage" when the router incriminates itself, but often the truthful answer is closer to
   "something broke, and here is the evidence." Verdicts arrive with a confidence story
   attached; presenting them as a confident one-word answer would misrepresent the data.

Classification values a consumer must handle: `healthy`, `unknown`, `local_problem`,
`dns_specific_failure`, `partial_path`, `wan_isp_outage`. Outages also carry a
`classification_history` — the verdict can change *during* an event.

Traceroute payloads attach to `outage_start` asynchronously, so an event can exist for tens of
seconds before its traceroute arrives. Hops distinguish "silent timeout" from "unreachable,
reported by X" — different findings that should not render the same way.

---

## 8. Open design problems

Ranked by how much they affect what can be built:

1. **Device identity.** Vendors resolve, but duplicates are indistinguishable and no labelling
   mechanism exists. Is the device list a first-class surface or a secondary one? If naming
   matters — and it probably does, since throttling in §9 is meaningless until a person can
   say *which* device — it needs the first write endpoint in the product.
2. **Expressing uncertainty.** The most honest verdict is often "something is wrong and here is
   what corroborates it." Reducing that to a green/red dot throws away the useful part.
3. **The empty state is the normal state.** A healthy home network produces a flat chart, no
   events, and no alerts. That is the view the owner sees 99% of the time, and it is the one
   most likely to be under-designed.
4. **Two clocks.** "Is it broken right now" and "what happened last Tuesday" are different
   products. Trying to serve both in one view is the main structural risk.
5. **Bandwidth without a total.** Only this PC's throughput exists. Whether that deserves
   real estate, or an honest empty state explaining the hardware limitation, is a real choice.
6. **No configuration UI.** Thresholds, probe targets, retention, and alert rules are all TOML
   only.

---

## 9. Out of scope

- **Throttling.** Needs hardware that does not exist yet — a ~$150 mini PC acting as gateway,
  or a router swap. Documented in `docs/spike-gateway.md`.
- **ARP-spoof MITM** to obtain per-device traffic. Technically possible, degrades network
  reliability for everyone in the house, and is rejected on purpose.
- **Authentication UI.** Loopback is exempt; a local dashboard needs no login screen.
