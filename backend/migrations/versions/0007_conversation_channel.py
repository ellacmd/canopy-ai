"""Separate customer calls from text conversations.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "conversations", sa.Column("channel", sa.String(10), nullable=False, server_default="chat")
    )


def downgrade():
    op.drop_column("conversations", "channel")
