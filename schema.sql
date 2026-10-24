CREATE TABLE IF NOT EXISTS autopilot_workspaces (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS autopilot_workspaces_updated_at
    ON autopilot_workspaces(updated_at);
