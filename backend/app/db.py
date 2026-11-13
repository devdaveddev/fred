from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection

from .config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def get_conn() -> Iterator[Connection]:
    with engine.begin() as conn:
        yield conn
