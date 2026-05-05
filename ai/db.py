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
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            if statement_timeout_ms:
                cur.execute(f"SET statement_timeout = {int(statement_timeout_ms)}")
            yield cur
