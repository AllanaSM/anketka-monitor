"""Read-only identity checks for the PostgreSQL migration target."""
from __future__ import annotations

import os
from urllib.parse import unquote, urlsplit

import psycopg


EXPECTED_ENV = {
    "database": "ANKETKA_EXPECTED_DB_NAME",
    "user": "ANKETKA_EXPECTED_DB_USER",
    "host": "ANKETKA_EXPECTED_DB_HOST",
    "port": "ANKETKA_EXPECTED_DB_PORT",
}


def parse_database_url(database_url: str) -> dict[str, str | int | None]:
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgresql", "postgres"}:
        raise ValueError("DATABASE_URL must use the postgresql:// scheme")
    if not parsed.hostname or not parsed.username or not parsed.path.strip("/"):
        raise ValueError("DATABASE_URL must include host, user and database name")
    return {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "user": unquote(parsed.username),
        "database": unquote(parsed.path.lstrip("/")),
    }


def expected_target() -> dict[str, str | int]:
    missing = [name for name, env_name in EXPECTED_ENV.items() if not os.environ.get(env_name)]
    if missing:
        names = ", ".join(EXPECTED_ENV[name] for name in missing)
        raise RuntimeError(f"Expected database identity is not configured: {names}")
    try:
        port = int(os.environ[EXPECTED_ENV["port"]])
    except ValueError as exc:
        raise RuntimeError("ANKETKA_EXPECTED_DB_PORT must be an integer") from exc
    return {
        "database": os.environ[EXPECTED_ENV["database"]],
        "user": os.environ[EXPECTED_ENV["user"]],
        "host": os.environ[EXPECTED_ENV["host"]],
        "port": port,
    }


def verify_identity(conn: psycopg.Connection, database_url: str) -> None:
    target = parse_database_url(database_url)
    expected = expected_target()
    actual_database, actual_user, server_port = conn.execute(
        "SELECT current_database(), current_user, inet_server_port()"
    ).fetchone()

    mismatches = []
    for key in ("database", "user", "host", "port"):
        actual = target[key]
        if key == "database":
            actual = actual_database
        elif key == "user":
            actual = actual_user
        if actual != expected[key]:
            mismatches.append(f"{key}: expected {expected[key]!r}, got {actual!r}")

    projects_exists = conn.execute(
        "SELECT to_regclass('public.projects') IS NOT NULL"
    ).fetchone()[0]
    if projects_exists:
        project_exists = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM projects WHERE code = %s)",
            (os.environ.get("ANKETKA_EXPECTED_PROJECT_CODE", "anketka"),),
        ).fetchone()[0]
        if not project_exists:
            mismatches.append("project marker: expected project code 'anketka' was not found")
    else:
        print("[preflight] projects table is not present yet; expected on a migrated database")

    print(
        "[preflight] target "
        f"host={target['host']} port={target['port']} "
        f"database={actual_database} user={actual_user} server_port={server_port}"
    )
    if mismatches:
        details = "; ".join(mismatches)
        raise RuntimeError(f"PostgreSQL target identity check failed: {details}")
    print("[preflight] PostgreSQL target identity is accepted")


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    try:
        with psycopg.connect(database_url) as conn:
            verify_identity(conn, database_url)
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
