# AGENTS.md — conventions for netmon

Read [ROADMAP.md](ROADMAP.md) before starting work. It is authoritative on scope and phase
ordering. Do not implement future phases early.

## The rule that matters most

**All I/O and all clock reads are injected, never called directly.**

Collectors receive their probes and a clock object through their constructor. A collector that
calls `socket`, `subprocess`, `httpx`, or `time.time()` directly is wrong, even if it works —
it cannot be tested without a network and a real wall clock, and the suite will rot.

```python
class LatencyCollector(Collector):
    def __init__(self, icmp: IcmpProbe, clock: Clock, config: LatencyConfig) -> None: ...
```

Tests then inject `FakeIcmpProbe` and `FrozenClock` and assert on what reached the store.

## Layering

- **`probes/`** — one job each: perform an I/O operation and return structured data. No
  scheduling, no persistence, no config knowledge, no retry policy. Platform differences are
  resolved here behind a shared Protocol, so nothing upstream branches on `sys.platform`.
- **`collectors/`** — scheduled work. Wrap probes, apply config, emit `Sample` objects.
- **`db/`** — persistence only. No network awareness.
- **`api/`** — reads the store. Never invokes probes inline on a request path except via the
  explicit diagnostics endpoints.

## Platform notes

- ICMP on Windows uses `IcmpSendEcho` from `iphlpapi.dll` via `ctypes`. Do **not** use raw
  sockets — they require administrator privileges, and this tool must run unelevated.
- ARP on Windows uses `GetIpNetTable` via `ctypes`; parsing `arp -a` output is the fallback,
  not the primary path.
- Every ctypes call needs a POSIX counterpart behind the same Protocol so the suite runs on
  Linux CI.

## Testing

- `tests/unit/` — hermetic. No sockets, no subprocesses, no sleeping. Parser tests run against
  captured fixture bytes committed under `tests/fixtures/`.
- `tests/integration/` — fake collectors, real temporary SQLite, FastAPI `TestClient`.
- `tests/contract/` — OpenAPI snapshot, so an API change that would break the dashboard fails
  the build.
- Anything touching the real network is marked `@pytest.mark.live` and excluded by default.
- Coverage gate: 80% on `src/netmon/`.

Write tests alongside the code, not after the phase. A phase is not done when the code runs;
it is done when the tests prove it runs.

## Changelog

Every change updates the `[Unreleased]` section of [CHANGELOG.md](CHANGELOG.md) using Keep a
Changelog headings (`Added` / `Changed` / `Fixed` / `Removed`). When a phase ships, its
roadmap checkboxes are ticked in the same commit.

## Style

- Python 3.12+, full type annotations. `mypy --strict` must pass.
- `ruff check` and `ruff format` clean.
- Comments explain *why*, not *what*. Skip the ones that restate the code.
- Conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`.

## Scope discipline

If the roadmap says a capability is out of scope or gated, it stays that way. In particular:
do not implement ARP spoofing, packet interception, or any traffic-shaping enforcement. The
throttling interface may be defined; no enforcement backend gets written without an explicit
decision recorded in the roadmap.
