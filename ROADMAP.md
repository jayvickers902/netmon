# Roadmap

Phases are versioned and shipped in order. Each phase ends with a CHANGELOG entry, a green
test suite, and a working dashboard — no phase leaves the tree in a half-wired state.

Legend: `[ ]` not started · `[~]` in progress · `[x]` shipped

## Design freeze

**No new UI or visual design work lands before v0.6.** Phases v0.4 and v0.5 ship their data
through the REST API and the existing minimal dashboard only — no new pages, no new charts, no
restyling. The dashboard is deliberately being left ugly.

All interface and interaction design is deferred to v0.6, where the accumulated API surface is
put through a formal Claude Design review before anything is chosen and built. Building UI
incrementally per-phase would mean designing around whatever happened to exist at the time, and
then throwing it away. Any phase that needs to expose something to a human exposes it as JSON
and stops there.

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

## v0.3 — Connection health `[x]`

The troubleshooting payoff. This is the phase that earns the tool its keep.

- [x] `collectors/latency.py` — RTT, jitter, packet loss per configured target
      (gateway, 1.1.1.1, 8.8.8.8, and any custom hosts)
- [x] `collectors/dns_probe.py` — resolution timing across configured resolvers
- [x] `collectors/http_probe.py` — TTFB and status for configured URLs
- [x] `probes/upnp.py` — SSDP discovery pinned to the LAN interface, plus `WANIPConnection`
      `GetStatusInfo` / `GetExternalIPAddress`. **Must set `IP_MULTICAST_IF` and bind to the LAN
      address**; this host runs VPNs and the default route swallows the multicast otherwise
      (see `docs/spike-upnp.md`)
- [x] `collectors/wan_status.py` — router-reported WAN uptime, `LastConnectionError`, external
      IP. Uptime moving *backwards* is a `wan_reconnect` event: ground truth from the router
      that the link actually dropped, as opposed to a local Wi-Fi/NIC glitch or a dead far end.
      Promoted here from v0.4 by the UPnP spike
- [x] Outage detection: sustained-failure state machine with hysteresis, not single-sample
      triggers. Emits `outage_start` / `outage_end` events with accurate timestamps.
      Takes `wan_status` as a corroborating input, not just latency
- [x] **Auto-traceroute on degradation** — capture the path at the moment things break,
      not minutes later when you finally look. Attached to the outage event
- [x] `GET /events` and a dashboard timeline

## v0.4 — Bandwidth `[x]` *(rescoped twice — see both spikes)*

The UPnP spike came back negative: the eero 7 advertises `WANCommonInterfaceConfig` but
implements none of it — `GetTotalBytesSent`/`GetTotalBytesReceived` are not declared and
every action on that service returns HTTP 500. **Total WAN throughput has no local source
on the current topology.** Full detail in `docs/spike-upnp.md`.

The eero capability survey (`docs/eero-capabilities.md`) then reopened one option that the
earlier roadmap had rejected. Per-device usage is **not** an eero Plus feature — live per-device
throughput, refreshed every few seconds, is free tier, and it is served by the same cloud API
the mobile app uses. That makes it the only source of per-device bandwidth available without
buying hardware. It comes with real costs (reverse-engineered, cloud-dependent, needs a
non-Amazon eero account), so it ships **off by default**.

- [x] `collectors/host_nic.py` — this host's per-NIC and per-process counters via psutil.
      Unaffected by either spike, but covers this machine only
- [x] `collectors/speedtest.py` — scheduled throughput test, infrequent and opt-in
      (it saturates the link; running it often would itself cause the problems being measured).
      Measures *capacity*, not utilisation — it does not substitute for WAN counters
- [x] `probes/eero_cloud.py` + `collectors/eero_usage.py` — **opt-in, disabled by default.**
      Per-device up/down rates from `api-user.e2ro.com`. Non-negotiables:
  - Ships disabled. Enabling it is a deliberate config edit, never a default
  - Token-based: a one-time interactive `netmon eero-login` stores the long-lived token.
    The account password is never persisted, and the token never appears in logs or API responses
  - Poll no faster than 30s. The app's 5s cadence is not ours to imitate against an
    undocumented, unrate-limited endpoint
  - **Fails soft.** The internet being down is exactly when netmon matters most and exactly
    when this collector cannot work. It degrades to `unavailable`; it never stalls the
    scheduler, never raises, and never contributes to outage classification
  - Every stored sample is tagged with its source, so cloud-derived data is never silently
    mixed with locally measured data
- [x] **Spike:** throttling enforcement options for the hardware actually present —
      answered in `docs/spike-gateway.md`. Summary: nothing on the current topology can
      rate-limit; the eero has no web UI to automate and no local API. Real shaping needs a
      Linux gateway (~$160). eero profiles + schedules provide binary pause today. Feeds v1.1
- **No dashboard work.** Throughput data is exposed over REST only — see the design freeze above

Rejected sources, recorded so they are not re-litigated:

- **UPnP byte counters** — not implemented by the eero. Re-open only if firmware adds them
- **SNMP** — port 161 closed; not supported on any eero model
- **Local management API** — none exists. Port 80 serves a redirect to `blocked.eero.com`,
  not an admin UI. Confirmed by direct probe, not assumed
- **eero Plus subscription** — buying it adds *historical* usage graphs. netmon keeps its own
  history in SQLite; that is the point of the project. Not a data unlock
- **ARP-spoof interception** for per-device accounting — degrades the network for the target
  device and is hostile to debug. Out of scope by default, permanently

## v0.5 — Alerts & retention `[x]`

- [x] `alerts/rules.py` — threshold and sustained-condition evaluation, configurable per metric.
      Sustained-condition, not single-sample: the same hysteresis discipline as outage detection,
      because an alerting system that cries wolf gets muted and then it may as well not exist
- [x] `alerts/notifiers.py` — log, webhook, Windows toast. Each notifier fails independently;
      a dead webhook must not suppress the toast
- [x] Alert de-duplication and cool-down, so one flapping condition is one alert, not two hundred
- [x] `db/retention.py` — raw 7d → 1-min rollup 30d → 5-min rollup 1y, then purge. Rollups are
      computed, verified, and only then is the raw data deleted — never the other way round
- [x] `GET /alerts` — alert history over REST
- **No dashboard work.** Alert history is API-only in this phase; the UI lands in v0.6

## v0.6 — Interface design `[ ]` *(design phase — gated)*

**Ships after v1.0, not before it.** This is a deliberate reordering, recorded rather than done
silently: v1.0 is entirely non-visual, so building it first means the design review runs against
a complete and hardened API surface instead of one still being changed underneath it. Auth in
particular alters what the interface has to account for. The design freeze is the reason the
swap costs nothing — no UI exists yet to be invalidated.

Everything visual accumulates here. By this point v0.2–v0.5 have produced the full REST surface
— devices, metrics, events, outages, throughput, alerts — and the dashboard is still the
deliberately-ugly minimum from v0.2.

- [ ] Compile the complete API surface and the questions a user actually asks of it
- [ ] **Formal Claude Design review** of the whole interface at once, before anything is built
- [ ] Choose a direction from that review — explicitly, as a decision, not by accretion
- [ ] Build it: per-device drill-down, unified timeline across outages/joins/alerts, throughput
      charts scoped honestly to what is measured, on-demand diagnostics runner

## v1.0 — Hardening `[x]`

- [x] Run as a Windows service (Task Scheduler or NSSM), with install/uninstall scripts
- [x] API authentication — the dashboard should not be wide open on the LAN
- [x] Database backup and restore
- [x] Complete operator documentation

## v1.1+ — Throttling `[ ]` *(gated — requires a decision)*

**Not started unilaterally.** Traffic shaping requires an enforcement point the current
topology does not have. This phase delivers the interface and the analysis; the
implementation waits on a hardware decision.

- [ ] `throttle/base.py` — `Enforcer` interface: `apply(mac, down_kbps, up_kbps)`, `clear(mac)`
- [ ] `throttle/null.py` — no-op default that logs intent, so the API and UI can be built and
      tested before any enforcement backend exists
- [x] Written evaluation of the realistic options — **done, in `docs/spike-gateway.md`.**
      Conclusion: a Linux gateway is the only option that does real rate-limiting.
      A fanless 4-port N100 mini PC is ~$150–200 and 6–12 W (~$12/year), running Debian with
      `tc`/`cake` and `nftables`. **Buy 2.5GbE** — the eero 7 has 2.5GbE ports and a
      gigabit-only gateway caps the house permanently.
      Rejected: WSL2 (architecturally wrong — cannot own two interfaces, dies on host reboot);
      Hyper-V VM (works, but makes household internet depend on a workstation's uptime);
      eero web-UI automation (**no web UI exists** — port 80 is a block-page redirect);
      ARP-spoofing (degrades the target's connection)
- [ ] `throttle/linux_tc.py` — the chosen backend, once hardware exists
- [ ] `collectors/gateway_nic.py` — per-device byte counters from nftables. Free once a gateway
      exists, and strictly better than the cloud API: local, exact, no subscription, no
      reverse-engineering
- [ ] **Second `wan_status` backend.** Bridging the eero disables UPnP, which is where v0.3 gets
      its WAN uptime signal. A gateway owns the WAN interface and can report link state
      directly — but this is real work, not a free swap, and the outage classifier regresses
      until it is done

---

## Out of scope

- **Per-device bandwidth accounting from a passive LAN host.** The switch does not forward
  other devices' frames to us; no amount of code changes that. Two paths exist and both are
  on the roadmap: the eero cloud API (v0.4, opt-in, cloud-dependent) and nftables counters on
  a gateway (v1.1, local and exact).
- **Total WAN throughput** on the current topology. The eero implements no byte counters and
  offers no local management API. Resolved by the v1.1 gateway decision.
- **Traffic shaping on the current topology.** There is no enforcement point. See
  `docs/spike-gateway.md` §5.
- Deep packet inspection or content logging.
- WAN-side/remote access. netmon is a LAN tool.

## Hardware context

The network is an **eero 7 mesh** (dual-band BE5000, 2× 2.5GbE ports per unit), not the
ISP-provided router originally assumed. Surveyed in full in `docs/eero-capabilities.md`.
Confirmed by direct probe rather than assumption: no SSH (22 closed), no SNMP (161 closed),
**no local management API of any kind** — port 80 returns a `303` redirect to
`blocked.eero.com`, which is the content-filter block page, not an admin interface. What is
open locally is DNS (53) and SSDP (1900). UPnP `WANIPConnection` is therefore netmon's only
local router-derived signal, and v0.3 uses it.

Three things follow:

1. **The app is more capable than the network interface.** Live per-device usage, profiles,
   pause, schedules, IP reservations, custom DNS, SQM and local DNS caching are all there —
   they are just not reachable from the LAN. Configuration stays a phone job; netmon measures.
2. **eero Labs is free and worth enabling.** Smart Queue Management is bufferbloat control, and
   bufferbloat under load is the most likely cause of the original "connection is up and down"
   complaint. netmon's latency collector is the instrument for measuring whether it helped.
3. **v1.1 narrows to one option.** There is no web UI to automate and no local API to drive, so
   a Linux gateway is the only path to real shaping. `docs/spike-gateway.md` prices it at
   ~$160 and 8 W and recommends deferring the purchase until rate-limiting is actually wanted —
   binary pause via eero profiles covers most of the intent today.
