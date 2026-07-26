CREATE TABLE IF NOT EXISTS device (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mac TEXT NOT NULL UNIQUE,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    vendor TEXT,
    hostname TEXT,
    label TEXT,
    is_tracked INTEGER NOT NULL DEFAULT 1,
    missed_sweeps INTEGER NOT NULL DEFAULT 0,
    is_online INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS device_ip (
    device_id INTEGER NOT NULL REFERENCES device(id) ON DELETE CASCADE,
    ip TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (device_id, ip)
);

CREATE TABLE IF NOT EXISTS metric (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    target TEXT NOT NULL,
    unit TEXT NOT NULL,
    UNIQUE (kind, target, unit)
);

CREATE TABLE IF NOT EXISTS sample (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_id INTEGER NOT NULL REFERENCES metric(id) ON DELETE CASCADE,
    ts REAL NOT NULL,
    value REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS sample_metric_ts ON sample(metric_id, ts);

CREATE TABLE IF NOT EXISTS event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    device_id INTEGER REFERENCES device(id) ON DELETE SET NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS event_ts ON event(ts);
