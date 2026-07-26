# Roadmap

Phases are versioned and shipped in order. Each phase ends with a CHANGELOG entry, a green
test suite, and a working dashboard — no phase leaves the tree in a half-wired state.

Legend: `[ ]` not started · `[~]` in progress · `[x]` shipped

---

## v0.1 — Foundation `[x]`

Scaffolding and the skeleton everything else hangs off.

- [x] `pyproject.toml` with uv, ruff, mypy, pytest, coverage config
- [x] `config.py` — pydantic-settings loading `netmon.toml`, with schema validation
- [x] SQLite store: WAL mode, connection management, `schema.sql`, forward-only migrations
- [x] `scheduler.py` — asyncio supervisor with per-collector intervals, startup jitter,
      exponential backoff, and failure isolation (one bad collector must not stop the others)
- [x] FastAPI app with lifespan-managed scheduler, `/health`
- [x] CI: lint, typecheck, test on push
- [x] **Spike:** SSDP M-SEARCH against the local router to determine whether it exposes
      `WANCommonInterfaceConfig` byte counters. **Answered 2026-07-26: no.** The router is an
      eero 7 and the service is an unimplemented stub. `WANIPConnection` does work and yields
      WAN uptime / last error / external IP. Scope changes pushed into v0.3 and v0.4;
      full write-up in `docs/spike-upnp.md`

## v0.2 — Discovery `[x]`

Know what's on the network.

- [x] `probes/arp.py` — `GetIpNetTable` via ctypes on Windows, `ip neigh`/`arp -a` fallback
- [x] `probes/icmp.py` — `IcmpSendEcho` via ctypes on Windows, POSIX fallback; no admin needed
- [x] `probes/oui.py` — bundled OUI database, MAC → vendor lookup
- [x] `collectors/discovery.py` — subnet sweep, ARP correlation, hostname resolution
      (reverse DNS, mDNS, NetBIOS)
- [x] Device identity keyed by **MAC**, not IP — DHCP leases move
- [x] `GET /devices`, `GET /devices/{id}`, device join/depart events
- [x] Dashboard: live device list with vendor, hostname, last-seen, online indicator

## v0.3 — Connection health `[ ]`

The troubleshooting payoff. This is the phase that earns the tool its keep.

- [ ] `collectors/latency.py` — RTT, jitter, packet loss per configured target
      (gateway, 1.1.1.1, 8.8.8.8, and any custom hosts)
- [ ] `collectors/dns_probe.py` — resolution timing across configured resolvers
- [ ] `collectors/http_probe.py` — TTFB and status for configured URLs
- [ ] `probes/upnp.py` — SSDP discovery pinned to the LAN interface, plus `WANIPConnection`
      `GetStatusInfo` / `GetExternalIPAddress`. **Must set `IP_MULTICAST_IF` and bind to the LAN
      address**; this host runs VPNs and the default route swallows the multicast otherwise
      (see `docs/spike-upnp.md`)
- [ ] `collectors/wan_status.py` — router-reported WAN uptime, `LastConnectionError`, external
      IP. Uptime moving *backwards* is a `wan_reconnect` event: ground truth from the router
      that the link actually dropped, as opposed to a local Wi-Fi/NIC glitch or a dead far end.
      Promoted here from v0.4 by the UPnP spike
- [ ] Outage detection: sustained-failure state machine with hysteresis, not single-sample
      triggers. Emits `outage_start` / `outage_end` events with accurate timestamps.
      Takes `wan_status` as a corroborating input, not just latency
- [ ] **Auto-traceroute on degradation** — capture the path at the moment things break,
      not minutes later when you finally look. Attached to the outage event
- [ ] `GET /events` and a dashboard timeline

## v0.4 — Bandwidth `[ ]` *(scope reduced — see spike)*

The UPnP spike came back negative: the eero 7 advertises `WANCommonInterfaceConfig` but
implements none of it — `GetTotalBytesSent`/`GetTotalBytesReceived` are not declared and
every action on that service returns HTTP 500. **Total WAN throughput has no local source
on the current topology.** Full detail in `docs/spike-upnp.md`.

What survives:

- [ ] `collectors/host_nic.py` — this host's per-NIC and per-process counters via psutil.
      Unaffected by the spike, but covers this machine only
- [ ] `collectors/speedtest.py` — scheduled throughput test, infrequent and opt-in
      (it saturates the link; running it often would itself cause the problems being measured).
      Measures *capacity*, not utilisation — it does not substitute for WAN counters
- [ ] Dashboard throughput charts, scoped honestly to what is actually measured
- [ ] **Spike:** document concrete throttling enforcement options for the actual hardware
      present, in `docs/spike-throttle.md`. Feeds the v1.1 decision

Rejected sources, recorded so they are not re-litigated:

- **UPnP byte counters** — not implemented by the eero. Re-open only if firmware adds them
- **eero cloud API** (`api-user.e2ro.com`) — undocumented, token-authenticated, does expose
  per-device usage, but puts a cloud dependency and an unannounced-breakage risk into a LAN
  tool. Not recommended; not to be built speculatively

## v0.5 — Alerts & retention `[ ]`

- [ ] `alerts/rules.py` — threshold and sustained-condition evaluation, configurable per metric
- [ ] `alerts/notifiers.py` — log, webhook, Windows toast
- [ ] `db/retention.py` — raw 7d → 1-min rollup 30d → 5-min rollup 1y, then purge
- [ ] Alert history in the API and dashboard

## v0.6 — Dashboard polish `[ ]`

- [ ] Per-device drill-down: presence history, latency, associated events
- [ ] Unified timeline across outages, device joins/departures, and alerts
- [ ] On-demand diagnostics runner (ping / traceroute / DNS check from the UI)

## v1.0 — Hardening `[ ]`

- [ ] Run as a Windows service (Task Scheduler or NSSM), with install/uninstall scripts
- [ ] API authentication — the dashboard should not be wide open on the LAN
- [ ] Database backup and restore
- [ ] Complete operator documentation

## v1.1+ — Throttling `[ ]` *(gated — requires a decision)*

**Not started unilaterally.** Traffic shaping requires an enforcement point the current
topology does not have. This phase delivers the interface and the analysis; the
implementation waits on a hardware decision.

- [ ] `throttle/base.py` — `Enforcer` interface: `apply(mac, down_kbps, up_kbps)`, `clear(mac)`
- [ ] `throttle/null.py` — no-op default that logs intent, so the API and UI can be built and
      tested before any enforcement backend exists
- [ ] Written evaluation of the realistic options:
  - **Linux box as gateway** (e.g. Raspberry Pi) with `tc`/`nftables` — the correct solution;
    real per-device shaping, requires re-pointing DHCP
  - **Router's own QoS / parental controls**, driven by web-UI automation — no new hardware,
    but fragile and firmware-dependent
  - **ARP-spoof interception** — no new hardware, but degrades network reliability for the
    targeted device and is hostile to debug. Documented for completeness; **not recommended**
- [ ] Implementation of the chosen backend

---

## Out of scope

- **Per-device bandwidth accounting** on the current topology. Not achievable from a passive
  LAN host without router cooperation. Becomes available for free if the v1.1 gateway option
  is chosen, since a gateway sees all traffic by definition.
- **Total WAN throughput** on the current topology. The eero implements no byte counters and
  offers no local management API. Same resolution path as above: the v1.1 gateway decision.
- Deep packet inspection or content logging.
- WAN-side/remote access. netmon is a LAN tool.

## Hardware context

The network is an **eero 7 mesh**, not the ISP-provided router originally assumed. Practically
this is the same constraint — no SSH, no SNMP, no mirror port, no local API, cloud-managed —
so nothing in v0.1–v0.6 changes. It does narrow v1.1: eero exposes no supported hook for
traffic shaping, so the "router's own QoS via web-UI automation" option is weaker than it would
be on a conventional router, and eero's own per-device controls live behind its app and
subscription rather than any interface netmon could drive. The Linux-box-as-gateway option is
correspondingly more attractive.
