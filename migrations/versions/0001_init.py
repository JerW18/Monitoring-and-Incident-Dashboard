"""Initial schema: hosts, metrics, alert rules and incidents."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE = [
    # Hosts that have ever reported. The host-down rule checks each of these
    # against its Redis status key.
    """
    CREATE TABLE hosts (
        id         TEXT PRIMARY KEY,
        first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_seen  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE metrics (
        id      BIGSERIAL PRIMARY KEY,
        host_id TEXT NOT NULL REFERENCES hosts (id),
        name    TEXT NOT NULL,
        value   DOUBLE PRECISION NOT NULL,
        ts      TIMESTAMPTZ NOT NULL
    )
    """,
    # Chart history and per-host rule evaluation: latest samples of one metric for one host.
    "CREATE INDEX metrics_host_name_ts ON metrics (host_id, name, ts DESC)",
    # Threshold rules need all condition fields. host_down uses none of them,
    # because the Redis TTL is its duration.
    """
    CREATE TABLE alert_rules (
        id            SERIAL PRIMARY KEY,
        name          TEXT NOT NULL UNIQUE,
        kind          TEXT NOT NULL CHECK (kind IN ('threshold', 'host_down')),
        metric        TEXT,
        op            TEXT CHECK (op IN ('>', '<')),
        threshold     DOUBLE PRECISION,
        duration_secs INT CHECK (duration_secs > 0),
        cooldown_secs INT NOT NULL DEFAULT 60 CHECK (cooldown_secs >= 0),
        enabled       BOOLEAN NOT NULL DEFAULT TRUE,
        builtin       BOOLEAN NOT NULL DEFAULT FALSE,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT alert_rules_kind_fields CHECK (
            (kind = 'threshold' AND metric IS NOT NULL AND op IS NOT NULL
                AND threshold IS NOT NULL AND duration_secs IS NOT NULL)
            OR
            (kind = 'host_down' AND metric IS NULL AND op IS NULL
                AND threshold IS NULL AND duration_secs IS NULL)
        )
    )
    """,
    # The engine assumes a single host_down rule.
    "CREATE UNIQUE INDEX alert_rules_one_host_down ON alert_rules (kind) WHERE kind = 'host_down'",
    # RESTRICT: a rule with incident history cannot be deleted (disable it instead).
    """
    CREATE TABLE incidents (
        id              BIGSERIAL PRIMARY KEY,
        host_id         TEXT NOT NULL REFERENCES hosts (id),
        rule_id         INT NOT NULL REFERENCES alert_rules (id) ON DELETE RESTRICT,
        status          TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'acknowledged', 'resolved')),
        message         TEXT NOT NULL,
        opened_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        acknowledged_at TIMESTAMPTZ,
        resolved_at     TIMESTAMPTZ,
        CONSTRAINT incidents_ack_has_time
            CHECK (status <> 'acknowledged' OR acknowledged_at IS NOT NULL),
        CONSTRAINT incidents_resolved_has_time
            CHECK ((status = 'resolved') = (resolved_at IS NOT NULL))
    )
    """,
    # At most one active (open or acknowledged) incident per host+rule. Incident
    # creation uses INSERT ... ON CONFLICT DO NOTHING against this index.
    """
    CREATE UNIQUE INDEX incidents_one_active ON incidents (host_id, rule_id)
    WHERE status IN ('open', 'acknowledged')
    """,
    # Incident list, filtered by status, newest first.
    "CREATE INDEX incidents_status_opened ON incidents (status, opened_at DESC)",
    """
    INSERT INTO alert_rules (name, kind, cooldown_secs, builtin)
    VALUES ('Host down', 'host_down', 60, TRUE)
    """,
    """
    INSERT INTO alert_rules (name, kind, metric, op, threshold, duration_secs, cooldown_secs)
    VALUES ('High CPU', 'threshold', 'cpu', '>', 90, 30, 60)
    """,
]

DOWNGRADE = [
    "DROP TABLE incidents",
    "DROP TABLE alert_rules",
    "DROP TABLE metrics",
    "DROP TABLE hosts",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
