from sqlalchemy import Engine, inspect, text


def upgrade_schema(engine: Engine) -> None:
    """Apply the small, idempotent schema upgrades needed by the current MVP."""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "attractions" not in table_names:
        return

    columns = {column["name"] for column in inspector.get_columns("attractions")}
    planning_columns = (
        {column["name"] for column in inspector.get_columns("planning_jobs")}
        if "planning_jobs" in table_names
        else set()
    )
    with engine.begin() as connection:
        if "knowledge_status" not in columns:
            connection.execute(
                text(
                    "ALTER TABLE attractions "
                    "ADD COLUMN knowledge_status VARCHAR(16) NOT NULL "
                    "DEFAULT 'pending'"
                )
            )
        if "knowledge_error" not in columns:
            connection.execute(
                text("ALTER TABLE attractions ADD COLUMN knowledge_error TEXT NULL")
            )

        if "planning_jobs" in table_names:
            if "worker_id" not in planning_columns:
                connection.execute(
                    text("ALTER TABLE planning_jobs ADD COLUMN worker_id VARCHAR(64) NULL")
                )

    inspector = inspect(engine)
    indexes = {index["name"] for index in inspector.get_indexes("attractions")}
    if "ix_attractions_knowledge_status" not in indexes:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE INDEX ix_attractions_knowledge_status "
                    "ON attractions (knowledge_status)"
                )
            )

    if "planning_jobs" in table_names:
        planning_indexes = {index["name"] for index in inspector.get_indexes("planning_jobs")}
        if "ix_planning_jobs_worker_id" not in planning_indexes:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "CREATE INDEX ix_planning_jobs_worker_id "
                        "ON planning_jobs (worker_id)"
                    )
                )

    # Knowledge already created before this column was introduced is immediately ready.
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE attractions AS a "
                "SET a.knowledge_status = 'ready', a.knowledge_error = NULL "
                "WHERE EXISTS ("
                "SELECT 1 FROM attraction_knowledge AS k WHERE k.poi_id = a.poi_id"
                ")"
            )
        )
