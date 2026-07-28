# Operations guide

This guide covers the v1.0 deployment of netmon as a local Windows service. netmon is a LAN
tool; it does not provide WAN-side access.

## Install and run

From a checked-out project directory with Python 3.12+ and uv installed:

```powershell
uv sync
Copy-Item netmon.example.toml netmon.toml
uv run python -m netmon
```

The dashboard is served at `http://127.0.0.1:8787` by default. The REST API and OpenAPI
documentation are on the same address. The database defaults to `data/netmon.db`; paths in
`netmon.toml` are relative to the process working directory.

## Configure

The example file documents every current section. Configuration is loaded from `netmon.toml`
and then `NETMON_` environment variables override it, using double underscores for nested
fields. For example:

```powershell
$env:NETMON_SERVER__HOST = "192.168.4.143"
$env:NETMON_API__TOKEN = "your-generated-token"
```

If `server.host` is not loopback, `api.token` (or `NETMON_API__TOKEN`) is required at startup.
This prevents an accidental LAN-wide inventory service. A loopback-only server may run without
a token.

## Enable API authentication

Generate a token without writing it anywhere:

```powershell
uv run netmon gen-token
```

Put the printed value in `[api] token` or set `NETMON_API__TOKEN`. Requests use:

```text
Authorization: Bearer <token>
```

Tokens are intentionally not accepted in query strings because URLs end up in browser history,
proxy logs, and server logs. `/health` remains unauthenticated for service health checks. By
default, loopback requests are also exempt so the existing local dashboard needs no login. This
is a deliberate trade-off: anyone with a local shell already has access to the SQLite file. Set
`allow_loopback_unauthenticated = false` when local requests must authenticate too.

Missing or invalid credentials receive `401` with `WWW-Authenticate: Bearer`. The response does
not identify which credential failed.

## Label devices

Device labels are editable from the dashboard. API clients can use the same authenticated write
path:

```http
PATCH /devices/42
Content-Type: application/json

{"label":"Living room TV"}
```

Labels are trimmed and limited to 120 characters. Send `null` or an empty string to clear a
label. Discovery continues to manage the device's hostname, vendor, addresses, and presence;
the write endpoint changes only the user-assigned label.

## Run as a Windows service

The installer uses the built-in Task Scheduler and must be run from an elevated PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install-service.ps1 -PythonPath (Resolve-Path .venv\Scripts\python.exe)
```

The task starts at boot under `SYSTEM`, uses the repository as its working directory, restarts
failed runs, starts when available, and does not stop when the machine switches to battery power.
The task launches the same `python -m netmon` command used interactively. If Python is not in the
system task environment, pass its full path with `-PythonPath`.

Logs are written to `data/logs/netmon.out.log` and `data/logs/netmon.err.log`, with five rotated
backups at 10 MB per stream. To remove the task without touching data or configuration:

```powershell
.\scripts\uninstall-service.ps1
```

The uninstall script is idempotent. A service that will not start can usually be diagnosed by
reading the error log, then running `uv run python -m netmon` from the repository directory to
see the startup exception directly.

## Back up and restore

Create a consistent snapshot while netmon is running:

```powershell
uv run netmon backup data\backups\netmon-2026-07-26.db
```

Backups use SQLite `VACUUM INTO`, so the live WAL database does not need to be stopped. The
destination must not already exist. Restore only after stopping the scheduled task:

```powershell
.\scripts\uninstall-service.ps1
uv run netmon restore data\backups\netmon-2026-07-26.db
```

Restore verifies that the source is SQLite, passes `PRAGMA integrity_check`, and has a schema
version supported by this build. A newer schema is refused. The current database is moved to
`data/netmon.db.pre-restore` (or the next numbered name) rather than deleted. The source backup
is retained. A running netmon process is refused by the database lock marker; do not bypass that
check.

## Understanding metrics

Use `/metrics` to inspect samples. The `source` field tells you where each value came from:

| Source | Meaning |
|---|---|
| `local` | Locally measured latency, DNS, HTTP, and router-reported UPnP WAN status |
| `host_nic` | This machine's per-NIC and optional per-process counters from psutil |
| `speedtest` | Opt-in link-capacity and latency tests; not utilisation |
| `eero_cloud` | Opt-in cloud-reported per-device rates; unavailable during an internet outage |

The absence of a total-WAN counter is intentional. The eero 7 does not implement the UPnP byte
counter service, and a passive LAN host cannot see other devices' traffic. See
[`docs/spike-upnp.md`](spike-upnp.md) and the README capability table.

## Opt-in collectors

### Speedtest

Enable it deliberately because it consumes bandwidth while measuring capacity:

```toml
[speedtest]
enabled = true
interval_seconds = 21600
```

The interval must be at least 900 seconds. Results are capacity measurements, not live WAN
utilisation, and the configured byte limits and 120-second default budget protect against a
misbehaving endpoint.

### eero per-device usage

The collector is disabled by default and polls no faster than every 30 seconds. Set `enabled =
true`, then complete the one-time token flow:

```powershell
uv run netmon eero-login
```

The account must be a separate eero account created with an email address and password. Amazon
SSO accounts cannot use this cloud API; invite the separate account to the network as an admin,
as described in [eero capabilities section 3](eero-capabilities.md#3-the-cloud-api--the-per-device-bandwidth-unlock).
Only the long-lived token is stored at `eero.token_path`; the password is not persisted.

This is a reverse-engineered, cloud-dependent source. It fails soft and records
`eero_usage_available = 0` when the token is absent or the service is unavailable. It never
contributes to outage classification. See [eero-capabilities.md](eero-capabilities.md).

## Troubleshooting

### UPnP returns nothing

The UPnP probe must use the LAN interface. VPNs and tunnel adapters can capture multicast when
the default route is used, producing zero SSDP responses. Set both `wan.lan_ip` and `wan.gateway`
to the host's LAN address and router address, then restart netmon. The full investigation is in
[`docs/spike-upnp.md`](spike-upnp.md).

### eero collector reports unavailable

Check that `[eero] enabled = true`, the token file exists at `token_path`, and the account is an
email/password eero account rather than Amazon SSO. The collector also becomes unavailable when
the internet or eero cloud is down; this is expected and does not indicate a local database
failure. Inspect `/metrics` for the `eero_cloud` source and `eero_usage_available` value.

### The service does not start

Read both files under `data/logs`. Confirm the scheduled task's working directory is the project
root, that `-PythonPath` points to the environment containing netmon, and that `netmon.toml` is
present there. Run the exact interpreter manually with `python -m netmon`; configuration errors,
missing LAN settings, and a missing required LAN API token are reported at startup. Re-run the
installer as Administrator if the task was never registered.
