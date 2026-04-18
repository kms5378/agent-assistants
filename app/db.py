from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings
from app.models import Base, SchemaMigration


SCHEMA_BASELINE_VERSION = "0001_initial"
SCHEMA_BASELINE_DESCRIPTION = "create core assistant tables"


def build_engine(settings: Settings):
    connect_args = {}
    if settings.is_sqlite:
        connect_args["check_same_thread"] = False
    return create_engine(
        settings.database_url,
        echo=settings.log_sql,
        future=True,
        connect_args=connect_args,
        pool_pre_ping=not settings.is_sqlite,
    )


def build_session_factory(settings: Settings) -> sessionmaker[Session]:
    engine = build_engine(settings)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db(session_factory: sessionmaker[Session]) -> None:
    engine = session_factory.kw["bind"]
    Base.metadata.create_all(bind=engine)
    _apply_phase3_retry_columns(engine)
    session = session_factory()
    try:
        baseline = session.get(SchemaMigration, SCHEMA_BASELINE_VERSION)
        if baseline is None:
            session.add(
                SchemaMigration(
                    version=SCHEMA_BASELINE_VERSION,
                    description=SCHEMA_BASELINE_DESCRIPTION,
                )
            )
            session.commit()
    finally:
        session.close()


def _apply_phase3_retry_columns(engine) -> None:
    inspector = inspect(engine)
    dialect = engine.dialect.name
    reminder_columns = {column["name"] for column in inspector.get_columns("reminders")}
    delivery_columns = {column["name"] for column in inspector.get_columns("reminder_deliveries")}

    statements: list[str] = []
    statements.extend(
        _missing_column_statements(
            table_name="reminders",
            existing_columns=reminder_columns,
            dialect=dialect,
            columns=(
                ("attempt_count", _integer_column_sql(default=0)),
                ("last_error", "TEXT"),
                ("next_attempt_at", _datetime_column_sql(dialect)),
                ("max_attempts", _integer_column_sql(default=3)),
            ),
        )
    )
    statements.extend(
        _missing_column_statements(
            table_name="reminder_deliveries",
            existing_columns=delivery_columns,
            dialect=dialect,
            columns=(
                ("next_attempt_at", _datetime_column_sql(dialect)),
                ("max_attempts", _integer_column_sql(default=3)),
            ),
        )
    )

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _missing_column_statements(
    *,
    table_name: str,
    existing_columns: set[str],
    dialect: str,
    columns: tuple[tuple[str, str], ...],
) -> list[str]:
    statements: list[str] = []
    for column_name, column_sql in columns:
        if column_name in existing_columns:
            continue
        statements.append(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")
    return statements


def _integer_column_sql(*, default: int) -> str:
    return f"INTEGER NOT NULL DEFAULT {default}"


def _datetime_column_sql(dialect: str) -> str:
    if dialect == "postgresql":
        return "TIMESTAMP WITH TIME ZONE"
    return "DATETIME"


def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
