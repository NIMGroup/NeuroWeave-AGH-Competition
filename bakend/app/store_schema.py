"""Idempotent schema creation and additive migrations for LocalStore.

The connection factory owns commit/rollback. Existing rows and running job records
are preserved; schema initialization never deletes data or starts workers.
"""


def initialize_store(connect) -> None:
    statements = [
        """
            CREATE TABLE IF NOT EXISTS model_token_usage (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                turn_id TEXT,
                model TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                input_tokens INTEGER,
                output_tokens INTEGER,
                total_tokens INTEGER,
                reasoning_tokens INTEGER,
                cached_input_tokens INTEGER,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
                    ON DELETE CASCADE
            )
            """,
        """
            CREATE INDEX IF NOT EXISTS idx_model_token_usage_conversation_turn
            ON model_token_usage(conversation_id, turn_id)
            """,
        """
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                archived INTEGER NOT NULL DEFAULT 0,
                summary TEXT NOT NULL DEFAULT '',
                summary_version INTEGER NOT NULL DEFAULT 0,
                summarized_through_sequence INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        """
            CREATE TABLE IF NOT EXISTS conversation_messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                estimated_tokens INTEGER NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(conversation_id, sequence),
                FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
                    ON DELETE CASCADE
            )
            """,
        """
            CREATE INDEX IF NOT EXISTS idx_conversations_updated_at
            ON conversations(updated_at DESC)
            """,
        """
            CREATE TABLE IF NOT EXISTS datasets (
                dataset_id TEXT PRIMARY KEY,
                root_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                display_name TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_datasets_root_path
            ON datasets(root_id, relative_path)
            """,
        """
            CREATE TABLE IF NOT EXISTS workflows (
                workflow_id TEXT PRIMARY KEY,
                dataset_id TEXT,
                status TEXT NOT NULL,
                revision INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(dataset_id) REFERENCES datasets(dataset_id)
            )
            """,
        """
            CREATE INDEX IF NOT EXISTS idx_workflows_dataset_updated
            ON workflows(dataset_id, updated_at DESC)
            """,
        """
            CREATE TABLE IF NOT EXISTS workflow_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(workflow_id) REFERENCES workflows(workflow_id) ON DELETE CASCADE
            )
            """,
        """
            CREATE INDEX IF NOT EXISTS idx_workflow_events_workflow_revision
            ON workflow_events(workflow_id, revision)
            """,
        """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                workflow_revision INTEGER NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(workflow_id) REFERENCES workflows(workflow_id)
            )
            """,
        """
            CREATE INDEX IF NOT EXISTS idx_runs_workflow_updated
            ON runs(workflow_id, updated_at DESC)
            """,
        """
            CREATE INDEX IF NOT EXISTS idx_runs_active_status
            ON runs(status, updated_at DESC)
            WHERE status IN ('queued', 'running', 'cancel_requested')
            """,
        """
            CREATE TABLE IF NOT EXISTS run_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                progress REAL,
                step_id TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
            )
            """,
        """
            CREATE INDEX IF NOT EXISTS idx_run_events_run_event
            ON run_events(run_id, event_id)
            """,
    ]
    with connect() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        for statement in statements:
            connection.execute(statement)
        conversation_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(conversations)").fetchall()
        }
        if "archived" not in conversation_columns:
            connection.execute(
                "ALTER TABLE conversations ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
            )
        if "workspace_id" not in conversation_columns:
            connection.execute("ALTER TABLE conversations ADD COLUMN workspace_id TEXT")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversations_archived_updated_id "
            "ON conversations(archived, updated_at DESC, conversation_id DESC)"
        )
        run_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(runs)")
        }
        for name in ("storage_version", "state_revision"):
            if name not in run_columns:
                connection.execute(
                    f"ALTER TABLE runs ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0"
                )
        connection.execute("""CREATE TABLE IF NOT EXISTS run_plans (
                run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE, payload_json TEXT NOT NULL)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS run_steps (
                run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE, step_id TEXT NOT NULL,
                position INTEGER NOT NULL, payload_json TEXT NOT NULL, state_revision INTEGER NOT NULL,
                PRIMARY KEY(run_id,step_id))""")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_steps_revision ON run_steps(run_id,state_revision)"
        )
        connection.execute("""CREATE TABLE IF NOT EXISTS run_artifact_order (
                run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                artifact_id TEXT NOT NULL, position INTEGER NOT NULL,
                PRIMARY KEY(run_id,artifact_id))""")
        connection.execute("""CREATE TABLE IF NOT EXISTS execution_drafts (
                workflow_id TEXT PRIMARY KEY REFERENCES workflows(workflow_id) ON DELETE CASCADE,
                workflow_revision INTEGER NOT NULL, draft_revision INTEGER NOT NULL,
                payload_json TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        connection.execute("PRAGMA optimize")
