# netmon

A self-hosted monitoring tool for a home network. Discovers devices, tracks connection
health, records bandwidth, and surfaces it all through a web dashboard and REST API.

Built to answer three questions quickly:

- **"Is the internet down, or is it just me?"** — continuous latency/jitter/loss, DNS and
  HTTP probes, and an outage timeline with traceroutes captured at the moment things degraded.
- **"What's on my network?"** — device inventory keyed by MAC, with vendor, hostname, and
  presence history.
- **"What's using the connection?"** — modem-sourced total downstream bytes, per-NIC counters,
  and periodic throughput tests. Upstream utilisation is not covered.

## Status

Pre-release. See [ROADMAP.md](ROADMAP.md) for phases and [CHANGELOG.md](CHANGELOG.md) for
what has landed.

## Deployment context

netmon runs as an ordinary host on the LAN, behind a **Hitron CODA modem in bridge mode and an
eero 7 mesh** — no SSH, no mirror port, no SNMP assumed. Everything it collects it obtains by
active probing, ARP inspection, or unauthenticated local endpoints. The eero holds the public WAN
IP and exposes no local management API; the bridged Hitron remains reachable for DOCSIS data.

That constraint has real consequences, stated plainly:

| Capability | Supported | Notes |
|---|---|---|
| Device inventory & presence | Yes | ARP table + ICMP sweep, reverse DNS / NetBIOS names, MAC→vendor, editable labels |
| Connection health & troubleshooting | Yes | Latency, jitter, loss, DNS timing, HTTP probes, outage events, auto-traceroute |
| WAN up/down, uptime, external IP | Yes | eero UPnP `WANIPConnection` plus independent Hitron modem uptime. Distinguishes a real ISP outage from a local glitch |
| This host's bandwidth | Yes | Per-NIC and per-process counters |
| Link capacity | Yes | Scheduled speedtest — capacity, not utilisation |
| **Total WAN bandwidth** | **Downstream only** | Hitron `dsoctets` provides verified pre-NAT household downstream throughput; no upstream octet counter is exposed. See [docs/hitron-capabilities.md](docs/hitron-capabilities.md) |
| Per-device bandwidth | Yes (opt-in) | eero cloud live rates are available with a separate non-Amazon account; passive LAN visibility remains impossible |
| **Throttling** | **Not yet** | Requires a traffic enforcement point the current topology does not provide. See ROADMAP v1.1 |
| Alerts & retention | Yes | Sustained rules, independent notifications, verified SQLite rollups, and dashboard history |
| On-demand investigation | Yes | Distinguishes local-link, upstream, and upload contention. Attributes contention per-device only with opt-in eero cloud or managed-host telemetry; modem downstream totals are measured, while upstream utilisation remains unavailable |

The remaining upstream-accounting and enforcement gap resolves by putting a Linux box in the
traffic path as the gateway. That is the v1.1 hardware decision, and it is not taken unilaterally.
Passive per-device visibility remains unavailable without either the opt-in eero cloud source or
a gateway.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for dependency management
- Windows 10/11 or Linux (developed on Windows; probes have per-platform implementations)

No administrator privileges required — ICMP uses the Windows `IcmpSendEcho` API rather than
raw sockets.

## Quick start

```bash
uv sync
cp netmon.example.toml netmon.toml
uv run python -m netmon
```

Dashboard at http://127.0.0.1:8787, OpenAPI docs at `/docs`.

## Development

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy src
```

The default test run touches no network. Tests that need real network access are marked
`@pytest.mark.live` and are excluded from the default run and from CI:

```bash
uv run pytest -m live
```

## Architecture

Two layers, deliberately separated:

- **`probes/`** — low-level I/O primitives (ICMP, ARP, UPnP, DNS, traceroute). No scheduling,
  no persistence, no state. Each has a platform-appropriate implementation behind a common
  interface.
- **`collectors/`** — scheduled jobs that wrap probes and emit `Sample` records to the store.

Every collector receives its probes and its clock by dependency injection; none of them call
the network or read the wall clock directly. That is what allows the test suite to exercise
the full collector → store → API path against fakes with a frozen clock, and it is the single
most important convention in this codebase. See [ROADMAP.md](ROADMAP.md).

## License

Private project.
