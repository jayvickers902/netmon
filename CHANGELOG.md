# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- v0.3 connection health: injected DNS/HTTP/traceroute/UPnP probes, latency/DNS/HTTP/WAN
  collectors, hysteretic outage classification with WAN corroboration, background traceroute
  capture, `/events`, `/metrics`, `/status`, and a health dashboard.
- Project scaffolding: README, roadmap, changelog, packaging config, example configuration.
- v0.1 foundation: validated configuration, WAL SQLite storage, migrations, scheduler, API
  lifespan, CI workflow, and the UPnP discovery spike.
- v0.2 discovery: injectable ICMP/ARP/OUI/hostname probes, bounded subnet discovery, device
  presence events, REST device/event endpoints, and a dependency-free dashboard.

### Fixed

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
