"""One connection pool for the whole process, opened and closed with the app."""

from psycopg_pool import ConnectionPool

from app.config import get_settings

_pool: ConnectionPool | None = None


def open_pool() -> None:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            get_settings().database_url,
            min_size=1,
            max_size=10,
            open=True,
        )


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def get_pool() -> ConnectionPool:
    if _pool is None:
        raise RuntimeError("Connection pool is not open")
    return _pool


def ping() -> bool:
    """True only if a real query round-trips. Used by the health endpoint."""
    try:
        with get_pool().connection() as conn:
            return conn.execute("SELECT 1").fetchone()[0] == 1
    except Exception:
        return False
