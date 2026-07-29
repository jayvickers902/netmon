# Hitron CODA — capability survey

**Date:** 2026-07-28 · **Hardware:** Hitron CODA (modem-only, `modelName: "CODA"`), Eastlink HFC
**Question:** the eero surveys concluded netmon had no source for total WAN throughput and no
physical-layer evidence. Does the modem in front of the eero fill either gap?

**Answer: yes, both, with no authentication at all.** This supersedes the "no local source"
conclusion in [`spike-upnp.md`](spike-upnp.md) §Consequences, item 4.

---

## 1. Topology as actually measured

```
Eastlink HFC (fibre to node, coax drop)
   │
   ▼
Hitron CODA  192.168.100.1   ← bridge mode, DOCSIS 3.1 downstream / 3.0 upstream
   │  2.5GbE, full duplex
   ▼
eero 7       192.168.4.1     ← does DHCP + NAT, WAN IP 173.237.x.x (public, not CGNAT)
   │
   ▼
switch ──► rooms (mostly unused) ──► office switch ──► wired PCs
```

Two corrections to earlier assumptions:

- **The modem is already in bridge mode.** The eero holds the public IP directly. Nothing needs
  to change on the Hitron, and there is no double-NAT to unwind.
- **Not behind CGNAT.** `traceroute` hop 2 is `10.80.64.1`, which reads like carrier NAT but is
  just Eastlink's CMTS-side interface. `GetExternalIPAddress` on the eero and `api.ipify.org`
  agree on a routable `173.237.x.x` address. Inbound port forwarding would work if ever wanted.

The modem's own management address on Eastlink's network is `10.80.82.63/18`, gateway
`10.80.64.1` — that is the DOCSIS management stack, separate from the eero's WAN lease.

## 2. What the modem exposes

Port scan from `192.168.4.143`:

| Port | State | Notes |
|---|---|---|
| 80 / 443 | **open** | Same content on both. HTTPS uses a self-signed cert — verification must be off |
| 22 / 23 | closed | No shell |
| 161 (SNMP) | closed | Eastlink locks SNMP to the WAN side, as every cable ISP does |
| 8080 / 8443 | closed | — |

**`192.168.100.1` is reachable from the LAN even though the modem is bridged.** This is normal
DOCSIS behaviour — the modem answers on that address regardless of mode — but it is the whole
reason this works without touching the topology.

### The endpoints — all unauthenticated

Every path below returns JSON on a plain `GET`, no login, no cookie, no token. The HTML UI at
`/` is a login page; the data endpoints behind it simply do not check.

| Endpoint | Returns |
|---|---|
| `/data/dsinfo.asp` | Per-channel downstream: frequency, `signalStrength` (dBmV), `snr` (dB), **`dsoctets`**, `correcteds`, `uncorrect` |
| `/data/dsofdminfo.asp` | OFDM channel: PLC/NCP/MDC1 lock state, `plcpower`, SNR, **`dsoctets`**, `correcteds`, `uncorrect` |
| `/data/usinfo.asp` | Per-channel upstream: frequency, bandwidth, `modtype`, `scdmaMode`, `signalStrength`. **No octet counter** |
| `/data/usofdminfo.asp` | OFDMA upstream channels and state |
| `/data/getSysInfo.asp` | `swVersion`, `serialNumber`, `rfMac`, **`systemUptime`**, `systemTime` |
| `/data/getCMInit.asp` | DOCSIS registration state machine — `ranging`, `dhcp`, `timeOfday`, `downloadCfg`, `registration`, `bpiStatus` |
| `/data/getLinkStatus.asp` | Ethernet link to the eero: `LinkStatus`, `LinkDuplex`, `LinkSpeed` |
| `/data/getCmDocsisWan.asp` | CM management IP, netmask, gateway, DHCP lease duration |
| `/data/system_model.asp` | `modelName` |
| `/data/getLanInfo.asp` | Mostly `"TODO"` in bridge mode; `domainSuffix` only |

Several fields are the literal string `"TODO"` — Hitron ships placeholder values for anything the
firmware doesn't populate in this mode. **A parser must treat `"TODO"`, `"NA"`, and `"Unknow"`
[sic] as null**, not as data. `getConnectInfo.asp` returns `[]` in bridge mode.

No event-log endpoint is exposed unauthenticated. `getEventLog.asp` and ~20 other plausible names
all 404. The event log — where T3/T4 ranging timeouts would appear — lives behind the web login.
That is the one genuinely valuable thing still locked.

## 3. The throughput unlock

`dsoctets` is a **monotonic per-channel byte counter**. Summing it across the 24 QAM channels plus
the OFDM channel and differencing over time yields total household downstream throughput, measured
at the WAN edge, pre-NAT, for every device including wireless ones.

Verified live over a 20.3 s interval:

```
QAM   delta 22,162,383 bytes ->  8.73 Mbps
OFDM  delta    515,972 bytes ->  0.20 Mbps
TOTAL                            8.93 Mbps
```

This is the metric the v0.4 design wanted and could not source. It costs one HTTP request.

**Upstream has no equivalent.** `usinfo.asp` carries no octet counter, and there is no other
endpoint that does. Upstream throughput remains unavailable from the modem; `host_nic` still only
covers this machine. Accept the asymmetry or resolve it with the gateway decision.

Counters are 32-bit and will wrap. A delta collector must discard negative deltas as a wrap or a
modem reboot rather than recording a negative rate, and must cross-check `systemUptime` before
trusting a delta across a gap.

## 4. Line quality at time of survey

| Measure | Value | Spec / verdict |
|---|---|---|
| Downstream channels | 24 × QAM + 1 × OFDM (4K FFT) | Full DOCSIS 3.1 downstream |
| Downstream power | +6.2 to +10.1 dBmV | In spec (−15…+15). Slightly hot, not a problem |
| Downstream SNR | 38.6 – 39.0 dB | Good. 256-QAM needs ≥ 30 dB |
| Downstream uncorrectables | **0 on all 24 channels** | Clean |
| OFDM | locked (PLC/NCP/MDC1), SNR 40 dB, 2095 uncorrectable | Negligible against 171 M corrected |
| Upstream channels | 4 × 64-QAM ATDMA (2 × 6.4 MHz, 2 × 3.2 MHz) | **OFDMA disabled — DOCSIS 3.0 upstream only** |
| Upstream power | 38.5 – 39.3 dBmV | Healthy (ideal 35–45) |
| Ethernet to eero | 2500 Mbps full duplex | Matches eero 7's 2.5GbE |
| DOCSIS registration | all stages `Success`, BPI authorized | Clean registration |

**The coax plant is in good shape.** Nothing here explains an intermittent connection. That is
itself a result: it moves suspicion off the physical layer and onto the ISP path or something
local.

The upstream finding is the notable one: with OFDMA disabled, 2 × 6.4 MHz + 2 × 3.2 MHz of
64-QAM caps raw upstream around 90–100 Mbps regardless of the plan. Upstream is the constrained
direction and always will be until Eastlink enables OFDMA.

## 5. Why this matters for the outage classifier

netmon event `id=50`, 2026-07-28 08:43 UTC — a 59-minute degradation that was classified three
different ways in the space of an hour (`wan_isp_outage` → `dns_specific_failure` →
`partial_path`) and recorded:

```json
"wan_evidence": {"state": "available_no_positive_evidence", "used": []}
```

Cross-checking the two uptime counters against that window:

- Modem `systemUptime` 67 h 50 m → booted ≈ 2026-07-26 04:07. **Up throughout the outage.**
- eero WAN `Uptime` 1709236 s → connected since ≈ 2026-07-09. **Up throughout the outage.**

So neither device reported a link event, and the classifier had nothing to go on — exactly the
`used: []` it recorded. DOCSIS counters would have decided it: an uncorrectable spike or upstream
power climb during that hour means the coax plant; flat counters mean the fault is beyond the
CMTS and the classifier should stop blaming the WAN. Either way it becomes evidence instead of a
guess.

`systemUptime` is also a second, independent WAN-reconnect signal. `spike-upnp.md` treats the
eero's UPnP uptime as ground truth for link drops; two counters that can disagree are strictly
better than one that cannot be checked.

## 6. Integration shape

The existing `metric(kind, target, unit, source)` schema takes this without migration — a new
`source='hitron'` sits alongside `'local'`, `'host_nic'`, `'eero_cloud'`. Suggested:

| kind | target | unit |
|---|---|---|
| `wan_downstream_rate` | `wan` | `bytes_per_second` |
| `docsis_ds_snr` / `docsis_ds_power` | `ch:<channelId>` | `db` / `dbmv` |
| `docsis_ds_uncorrectables` | `ch:<channelId>` | `count` |
| `docsis_us_power` | `ch:<channelId>` | `dbmv` |
| `modem_uptime_seconds` | `modem` | `seconds` |
| `modem_link_speed` | `modem` | `mbps` |

Shape it as `probes/hitron.py` + `collectors/docsis.py`, mirroring `upnp.py` / `wan_status.py`.
Constraints worth writing into the code:

- **Fail soft.** The modem is on a different subnet reached through the eero; treat unreachable
  as "unavailable", never as an outage signal in itself.
- **Poll no faster than 30 s.** These are embedded endpoints on a device whose day job is keeping
  the house online.
- **Disable TLS verification** if using HTTPS (self-signed), or just use HTTP — same content, and
  the traffic never leaves the LAN.
- **Null out `"TODO"` / `"NA"` / `"Unknow"`** at parse time.
- Unlike the eero cloud collector, this has **no cloud dependency and no credentials**, so it
  keeps working during an outage — which is precisely when it is worth the most. It can be on by
  default.

## 7. Prior art

- [`hairyhenderson/hitron_coda_exporter`](https://github.com/hairyhenderson/hitron_coda_exporter) — Prometheus exporter, CODA-4x8x, Go. Assumes `cusadmin` auth
- [`csmarshall/netdata-plugin-hitron-coda56-cable-modem`](https://github.com/csmarshall/netdata-plugin-hitron-coda56-cable-modem) — Python, polls the same six endpoints **with no auth**, parallel fetch. Closest match to this hardware
- [`biochemie/coda45_scraper`](https://github.com/biochemie/coda45_scraper) — Python, telegraf/syslog/json output
- [`charlysan/cmstool`](https://github.com/charlysan/cmstool) — SNR/power statistics from modem web UIs generally

## Sources

- [Hitron CODA-4x8x user's guide](https://lightspeed.ca/wp-content/uploads/2021/09/Internet-Lightspeed-Hitron-CODA-4680.pdf)
- [Hitron CODA56 user manual](https://us.hitrontech.com/wp-content/uploads/2023/01/CODA56-UM.pdf)
- [Hitron CODA product page](https://us.hitrontech.com/products/consumers/docsis-3-1-cable-modem/)
- [Reading a cable modem's diagnostic page](https://www.howtogeek.com/240575/how-to-read-your-cable-modems-diagnostic-page-when-something-goes-wrong/)
