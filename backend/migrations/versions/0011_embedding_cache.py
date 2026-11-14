"""Increment 11: persistent embedding cache (restarts and redeploys don't re-embed the same text).

Revision ID: 0011
"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

UPGRADE = """
CREATE TABLE IF NOT EXISTS embedding_cache (
  text_hash   TEXT PRIMARY KEY,          -- sha1 of the text; the text itself isn't stored
  model       TEXT NOT NULL,
  vec         BYTEA NOT NULL,            -- float32, unit-normalised
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS embedding_cache;")
