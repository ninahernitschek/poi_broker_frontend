---users.db---
-- Fresh installations only. Never run against an existing database:
-- existing deployments are migrated with one-shot apply_*.sql scripts
-- (see docs/adr/adr_db_schema_migration_strategy.md).
CREATE TABLE
    user (
        id INTEGER NOT NULL,
        email VARCHAR(100),
        password VARCHAR(100),
        name VARCHAR(1000),
        role VARCHAR(20),
        reset_token VARCHAR(128),
        reset_token_expires INTEGER,
        email_verified INTEGER DEFAULT 0,
        email_verification_token VARCHAR(128),
        email_verification_token_expires INTEGER,
        password_changed_at INTEGER,
        PRIMARY KEY (id),
        UNIQUE (email)
    );
CREATE INDEX ix_user_email_verification_token ON user (email_verification_token);

CREATE TABLE
    favorite (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        locus_id TEXT NOT NULL,
        created_at INTEGER,
        group_id INTEGER REFERENCES favorite_group (id) ON DELETE SET NULL,
        UNIQUE (user_id, locus_id),
        FOREIGN KEY (user_id) REFERENCES user (id) ON DELETE CASCADE
    );
CREATE INDEX ix_favorite_group_id ON favorite (group_id);
CREATE INDEX ix_favorite_user_id ON favorite (user_id);
CREATE INDEX ix_favorite_locus_id ON favorite (locus_id);

CREATE TABLE
    favorite_group (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (user_id, name),
        FOREIGN KEY (user_id) REFERENCES user (id) ON DELETE CASCADE
    );
CREATE INDEX ix_favorite_group_user_id ON favorite_group (user_id);

CREATE TABLE
    watchlist (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        rules_json TEXT NOT NULL,
        sql_where TEXT NOT NULL,
        created_at INTEGER NOT NULL DEFAULT (strftime ('%s', 'now')),
        UNIQUE (user_id, name),
        FOREIGN KEY (user_id) REFERENCES user (id) ON DELETE CASCADE
    );
CREATE INDEX ix_watchlist_user_id ON watchlist (user_id);

CREATE TABLE 
    filter_bookmark (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name VARCHAR(200) NOT NULL,
        query_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE
	);
CREATE INDEX ix_filter_bookmark_user_id ON filter_bookmark (user_id);
CREATE UNIQUE INDEX IF NOT EXISTS uix_filter_bookmark_user_name
    ON filter_bookmark (user_id, name);

CREATE TABLE 
    user_settings (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        default_feature_plot_columns TEXT,
        last_selected_observatory_json TEXT,
        FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE,
        UNIQUE (user_id)
	);


CREATE TABLE
    user_observatory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name VARCHAR(100) COLLATE NOCASE NOT NULL,
        latitude REAL NOT NULL CHECK (latitude >= -90.0 AND latitude <= 90.0),
        longitude REAL NOT NULL CHECK (longitude >= -180.0 AND longitude <= 180.0),
        timezone_name VARCHAR(64) NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE,
        UNIQUE (user_id, name)
    );
CREATE INDEX ix_user_observatory_user_id ON user_observatory (user_id);

CREATE TABLE
    export_task (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        file_path VARCHAR(512),
        error_message TEXT,
        snapshot_mjd REAL NOT NULL,
        FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE
    );
CREATE INDEX ix_export_task_user_id ON export_task (user_id);
CREATE INDEX ix_export_task_status ON export_task (status);
CREATE INDEX IF NOT EXISTS idx_export_task_status_updated_at
    ON export_task (status, updated_at);
CREATE INDEX IF NOT EXISTS idx_export_task_status_created_at
    ON export_task (status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uix_export_task_one_active_per_user
    ON export_task (user_id)
    WHERE status IN ('PENDING', 'RUNNING');