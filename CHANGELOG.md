# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- v0.4 bandwidth: explicit-source host-NIC rates with reset-safe deltas, opt-in bounded
  Cloudflare capacity tests, and opt-in soft-failing eero cloud per-device rates; REST metrics
  now identify unit and source, and `netmon eero-login` stores only the long-lived token.
- v0.5 alerts and retention: sustained threshold rules with shared outage hysteresis,
  independent log/webhook/toast notification backends, verified raw/1-minute/5-minute SQLite
  rollups, scheduled retention, and filterable `/alerts` history. No dashboard files changed.
- v0.3 connection health: injected DNS/HTTP/traceroute/UPnP probes, latency/DNS/HTTP/WAN
  collectors, hysteretic outage classification with WAN corroboration, background traceroute
  capture, `/events`, `/metrics`, `/status`, and a health dashboard.
- Project scaffolding: README, roadmap, changelog, packaging config, example configuration.
- v0.1 foundation: validated configuration, WAL SQLite storage, migrations, scheduler, API
  lifespan, CI workflow, and the UPnP discovery spike.
- v0.2 discovery: injectable ICMP/ARP/OUI/hostname probes, bounded subnet discovery, device
  presence events, REST device/event endpoints, and a dependency-free dashboard.

### Fixed

- eero per-device usage would have been permanently unavailable in practice. `device_rates()`
  raised on any device missing `up_rate`/`down_rate`, and the collector maps every exception to
  "unavailable" — so one offline or idle device, which routinely carries no rate fields, blanked
  per-device usage for the entire network. Unusable devices are now skipped, and
  `eero_devices_reporting` records how many did report so a real schema change stays visible in
  the data instead of hiding behind `available = 1`.
- Speedtest could run far past its own time budget. The budget was enforced per phase rather
  than per run, and the upload phase ignored it entirely, POSTing the whole payload as one
  materialised buffer. A slow uplink could overrun a 120 s budget by more than 2×, which is
  precisely the condition the budget exists to prevent. One deadline now covers the whole run,
  the upload streams in chunks, and the reported rate is over bytes actually sent rather than
  bytes intended.
- The eero collector's success path shipped untested — metric names, units, source tags and the
  per-device fan-out were unverified. Now covered, along with the upload deadline and the
  exhausted-budget path.
- Windows ICMP probe reported every host as unreachable. `IcmpCreateFile` returns a 64-bit
  HANDLE, but no `restype` was declared, so ctypes truncated it to 32 bits; the truncated
  value is not `INVALID_HANDLE_VALUE`, so it passed validation and every `IcmpSendEcho` then
  failed against a garbage handle. The reply buffer was also sized to `ICMP_ECHO_REPLY` alone,
  with no room for the echoed payload. Latency collection and the outage classifier were
  affected — a healthy network could be classified as a local problem. The Windows path had no
  test coverage, which is how it shipped; it now has signature and buffer-contract regression
  tests.

### Changed

- eero capability survey written up in `docs/eero-capabilities.md`, from direct probing rather
  than assumption: no SSH, no SNMP, and no local management API — port 80 serves a redirect to
  the content-filter block page, not an admin UI. Reverses one earlier roadmap decision: live
  per-device usage is eero *free tier*, not an eero Plus feature, and is served by the cloud
  API. It is therefore the only per-device bandwidth source available without new hardware, and
  enters v0.4 as an opt-in collector that ships disabled.
- Gateway and throttling spike answered in `docs/spike-gateway.md`, closing the v0.4 spike item.
  WSL2 cannot act as a gateway (it owns neither interface and does not survive a host reboot);
  a Hyper-V VM can but ties household internet to a workstation's uptime; a fanless 4-port N100
  mini PC is ~$150–200 and 6–12 W. eero web-UI automation is impossible — there is no web UI.
- ROADMAP: design freeze added. No new UI lands before v0.6, which becomes a formal design
  phase gated on a Claude Design review of the complete API surface. v0.4 and v0.5 expose their
  data over REST only.
- v0.1 UPnP spike answered against the live network and written up in `docs/spike-upnp.md`.
  The router is an eero 7 whose `WANCommonInterfaceConfig` service is an unimplemented stub,
  so total WAN bandwidth has no local source. `WANIPConnection` does work, so router-reported
  WAN uptime, last connection error, and external IP move into v0.3 as an outage signal.
  ROADMAP v0.3/v0.4 and the README capability table revised to match.
- Documented the offline OUI dataset refresh procedure.

[Unreleased]: https://github.com/jayvickers/netmon/compare/HEAD
