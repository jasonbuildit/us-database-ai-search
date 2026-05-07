import os
from contextlib import contextmanager
from psycopg_pool import ConnectionPool

_DSN = (
    f"host={os.environ['PGHOST']} port={os.environ.get('PGPORT', '5432')} "
    f"user={os.environ['PGUSER']} password={os.environ['PGPASSWORD']} "
    f"dbname={os.environ['PGDATABASE']}"
)

pool = ConnectionPool(_DSN, min_size=1, max_size=8, kwargs={"autocommit": True})


@contextmanager
def readonly_cursor(statement_timeout_ms: int | None = None):
    with pool.connection() as conn:
        # Pool default is autocommit=True, which makes SET TRANSACTION
        # READ ONLY no-op past its own statement. Open an explicit txn
        # for the duration of the context instead.
        prev_autocommit = conn.autocommit
        conn.autocommit = False
        try:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                if statement_timeout_ms:
                    cur.execute(
                        f"SET LOCAL statement_timeout = {int(statement_timeout_ms)}"
                    )
                yield cur
        finally:
            try:
                conn.rollback()
            except Exception:
                pass
            conn.autocommit = prev_autocommit
