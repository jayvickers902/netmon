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

- v0.1 UPnP spike answered against the live network and written up in `docs/spike-upnp.md`.
  The router is an eero 7 whose `WANCommonInterfaceConfig` service is an unimplemented stub,
  so total WAN bandwidth has no local source. `WANIPConnection` does work, so router-reported
  WAN uptime, last connection error, and external IP move into v0.3 as an outage signal.
  ROADMAP v0.3/v0.4 and the README capability table revised to match.
- Documented the offline OUI dataset refresh procedure.

[Unreleased]: https://github.com/jayvickers/netmon/compare/HEAD
