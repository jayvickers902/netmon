# Roadmap

Phases are versioned and shipped in order. Each phase ends with a CHANGELOG entry, a green
test suite, and a working dashboard — no phase leaves the tree in a half-wired state.

Legend: `[ ]` not started · `[~]` in progress · `[x]` shipped

---

## v0.1 — Foundation `[ ]`

Scaffolding and the skeleton everything else hangs off.

- [ ] `pyproject.toml` with uv, ruff, mypy, pytest, coverage config
- [ ] `config.py` — pydantic-settings loading `netmon.toml`, with schema validation
- [ ] SQLite store: WAL mode, connection management, `schema.sql`, forward-only migrations
- [ ] `scheduler.py` — asyncio supervisor with per-collector intervals, startup jitter,
      exponential backoff, and failure isolation (one bad collector must not stop the others)
- [ ] FastAPI app with lifespan-managed scheduler, `/health`
- [ ] CI: lint, typecheck, test on push
- [ ] **Spike:** SSDP M-SEARCH against the local router to determine whether it exposes
      `WANCommonInterfaceConfig` byte counters. Record the finding in `docs/spike-upnp.md`.
      This gates the v0.4 bandwidth approach and must be answered early.

## v0.2 — Discovery `[ ]`

Know what's on the network.

- [ ] `probes/arp.py` — `GetIpNetTable` via ctypes on Windows, `ip neigh`/`arp -a` fallback
- [ ] `probes/icmp.py` — `IcmpSendEcho` via ctypes on Windows, POSIX fallback; no admin needed
- [ ] `probes/oui.py` — bundled OUI database, MAC → vendor lookup
- [ ] `collectors/discovery.py` — subnet sweep, ARP correlation, hostname resolution
      (reverse DNS, mDNS, NetBIOS)
- [ ] Device identity keyed by **MAC**, not IP — DHCP leases move
- [ ] `GET /devices`, `GET /devices/{id}`, device join/depart events
- [ ] Dashboard: live device list with vendor, hostname, last-seen, online indicator

## v0.3 — Connection health `[ ]`

The troubleshooting payoff. This is the phase that earns the tool its keep.

- [ ] `collectors/latency.py` — RTT, jitter, packet loss per configured target
      (gateway, 1.1.1.1, 8.8.8.8, and any custom hosts)
- [ ] `collectors/dns_probe.py` — resolution timing across configured resolvers
- [ ] `collectors/http_probe.py` — TTFB and status for configured URLs
- [ ] Outage detection: sustained-failure state machine with hysteresis, not single-sample
      triggers. Emits `outage_start` / `outage_end` events with accurate timestamps
- [ ] **Auto-traceroute on degradation** — capture the path at the moment things break,
      not minutes later when you finally look. Attached to the outage event
- [ ] `GET /events` and a dashboard timeline

## v0.4 — Bandwidth `[ ]`

- [ ] `probes/upnp.py` — SSDP discovery + `GetTotalBytesSent`/`GetTotalBytesReceived`
- [ ] `collectors/wan.py` — WAN throughput derived from counter deltas, with wrap handling
- [ ] `collectors/host_nic.py` — this host's per-NIC and per-process counters via psutil
- [ ] `collectors/speedtest.py` — scheduled throughput test, infrequent and opt-in
      (it saturates the link; running it often would itself cause the problems being measured)
- [ ] Dashboard throughput charts
- [ ] **Spike:** document concrete throttling enforcement options for the actual hardware
      present, in `docs/spike-throttle.md`. Feeds the v1.1 decision

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

- **Per-device bandwidth accounting** on the default topology. Not achievable from a passive
  LAN host without router cooperation. Becomes available for free if the v1.1 gateway option
  is chosen, since a gateway sees all traffic by definition.
- Deep packet inspection or content logging.
- WAN-side/remote access. netmon is a LAN tool.
