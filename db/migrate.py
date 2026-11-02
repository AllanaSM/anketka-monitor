"""Apply versioned PostgreSQL migrations in db/migrations."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import psycopg

MIGRATIONS = Path(__file__).with_name("migrations")
LOCK_KEY = 874_221_031


def migration_files():
    return sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    files = migration_files()
    if not files:
        raise SystemExit(f"No migration files found in {MIGRATIONS}")
    with psycopg.connect(database_url) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    checksum TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            applied = dict(conn.execute("SELECT version, checksum FROM schema_migrations"))
            for path in files:
                version = path.name
                digest = checksum(path)
                if version in applied:
                    if applied[version] != digest:
                        raise RuntimeError(f"Migration checksum changed after application: {version}")
                    continue
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)", (version, digest))
                conn.commit()
                print(f"applied {version}", flush=True)
            print(f"database is up to date ({len(files)} migration(s))", flush=True)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
