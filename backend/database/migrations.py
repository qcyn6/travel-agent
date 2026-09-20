from sqlalchemy import Engine, inspect, text


_LEGACY_ATTRACTION_COLUMNS = (
    "interest_tags",
    "address",
    "rating",
    "source",
    "knowledge_status",
    "knowledge_error",
    "knowledge_quality",
    "tourism_level",
    "core_scope",
    "environment",
    "first_seen_at",
    "updated_at",
)

_LEGACY_CITY_COLUMNS = (
    "suitable_days_min",
    "suitable_days_max",
)

# These columns were experimental metadata and are intentionally not part of
# the attraction planning schema.  Existing installations must drop them;
# new installations never create them because they are absent from the ORM.
_REMOVED_ATTRACTION_COLUMNS = (
    "data_source",
    "description_origin",
    "duration_origin",
    "quality_status",
    "quality_flags",
    "is_active",
    "coordinate_source",
    "parent_poi_id",
)

_REMOVED_ATTRACTION_INDEXES = (
    "ix_attractions_data_source",
    "ix_attractions_quality_status",
    "ix_attractions_is_active",
    "ix_attractions_coordinate_source",
    "ix_attractions_parent_poi_id",
)


def upgrade_schema(engine: Engine) -> None:
    """Migrate the catalog to the single-table attraction planning model."""

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    with engine.begin() as connection:
        if "cities" in table_names:
            current_columns = {
                column["name"] for column in inspect(connection).get_columns("cities")
            }
            for column in _LEGACY_CITY_COLUMNS:
                if column in current_columns:
                    connection.execute(text(f"ALTER TABLE cities DROP COLUMN {column}"))

        if "attractions" in table_names:
            columns = {
                column["name"] for column in inspector.get_columns("attractions")
            }

            if "tags" not in columns:
                connection.execute(
                    text("ALTER TABLE attractions ADD COLUMN tags JSON NULL")
                )
            if "description" not in columns:
                connection.execute(
                    text("ALTER TABLE attractions ADD COLUMN description TEXT NULL")
                )
            if "visit_duration_minutes" not in columns:
                connection.execute(
                    text(
                        "ALTER TABLE attractions "
                        "ADD COLUMN visit_duration_minutes INT NOT NULL DEFAULT 90"
                    )
                )
            # Copy the old split data before removing the legacy columns/table.
            if "interest_tags" in columns:
                connection.execute(
                    text(
                        "UPDATE attractions "
                        "SET tags = COALESCE(interest_tags, JSON_ARRAY()) "
                        "WHERE tags IS NULL"
                    )
                )
            if "attraction_knowledge" in table_names:
                connection.execute(
                    text(
                        "UPDATE attractions AS a "
                        "INNER JOIN attraction_knowledge AS k "
                        "ON k.poi_id = a.poi_id "
                        "SET a.description = k.description "
                        "WHERE (a.description IS NULL OR TRIM(a.description) = '') "
                        "AND k.description IS NOT NULL "
                        "AND TRIM(k.description) <> ''"
                    )
                )
            connection.execute(
                text(
                    "UPDATE attractions "
                    "SET tags = JSON_ARRAY() "
                    "WHERE tags IS NULL"
                )
            )
            connection.execute(
                text(
                    "UPDATE attractions "
                    "SET description = CONCAT(name, '，适合结合自然、人文与休闲体验。') "
                    "WHERE description IS NULL OR TRIM(description) = ''"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE attractions "
                    "MODIFY COLUMN tags JSON NOT NULL"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE attractions "
                    "MODIFY COLUMN description TEXT NOT NULL"
                )
            )
            current_columns = {
                column["name"]
                for column in inspect(connection).get_columns("attractions")
            }
            for column in (*_LEGACY_ATTRACTION_COLUMNS, *_REMOVED_ATTRACTION_COLUMNS):
                if column in current_columns:
                    connection.execute(
                        text(f"ALTER TABLE attractions DROP COLUMN {column}")
                    )

            if "attraction_knowledge" in table_names:
                connection.execute(text("DROP TABLE attraction_knowledge"))

            # The former facts table is no longer part of the application
            # model. Preserve the only planning field we still need before
            # dropping it.
            if "attraction_facts" in table_names:
                facts_columns = {
                    column["name"]
                    for column in inspect(connection).get_columns("attraction_facts")
                }
                if {"poi_id", "estimated_visit_minutes"}.issubset(facts_columns):
                    connection.execute(
                        text(
                            "UPDATE attractions AS a "
                            "INNER JOIN attraction_facts AS f ON f.poi_id = a.poi_id "
                            "SET a.visit_duration_minutes = CASE "
                            "WHEN f.estimated_visit_minutes BETWEEN 1 AND 720 "
                            "THEN f.estimated_visit_minutes ELSE a.visit_duration_minutes END"
                        )
                    )
                connection.execute(text("DROP TABLE attraction_facts"))

            # MySQL normally removes a single-column index with its column,
            # but explicitly drop leftovers so repeated upgrades are safe.
            attraction_indexes = {
                index["name"] for index in inspect(connection).get_indexes("attractions")
            }
            for index_name in _REMOVED_ATTRACTION_INDEXES:
                if index_name in attraction_indexes:
                    connection.execute(
                        text(f"ALTER TABLE attractions DROP INDEX {index_name}")
                    )

        if "planning_jobs" in table_names:
            planning_columns = {
                column["name"]
                for column in inspect(connection).get_columns("planning_jobs")
            }
            if "worker_id" not in planning_columns:
                connection.execute(
                    text("ALTER TABLE planning_jobs ADD COLUMN worker_id VARCHAR(64) NULL")
                )

    if "planning_jobs" in table_names:
        inspector = inspect(engine)
        planning_indexes = {
            index["name"] for index in inspector.get_indexes("planning_jobs")
        }
        if "ix_planning_jobs_worker_id" not in planning_indexes:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "CREATE INDEX ix_planning_jobs_worker_id "
                        "ON planning_jobs (worker_id)"
                    )
                )
