"""Versioned policy evidence and a frozen 768-dimensional embedding space.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

DOCUMENT_COLUMNS = (
    ("source_key", 60),
    ("content_sha256", 64),
    ("rules_version", 40),
    ("embedding_space", 100),
    ("chunker_version", 40),
)


def upgrade() -> None:
    postgres = op.get_context().dialect.name == "postgresql"
    if postgres:
        # Supabase may already have vector installed in extensions. Preserve its schema.
        op.execute("SET LOCAL search_path TO public, extensions")
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute("""DO $$ DECLARE vector_schema text; BEGIN
            SELECT n.nspname INTO vector_schema FROM pg_extension e
            JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='vector';
            EXECUTE format('GRANT USAGE ON SCHEMA %I TO service_role', vector_schema);
        END $$;""")
    with op.batch_alter_table("policy_documents") as batch:
        for name, length in DOCUMENT_COLUMNS:
            batch.add_column(sa.Column(name, sa.String(length), nullable=True))
        batch.create_unique_constraint("uq_policy_documents_source_key", ["source_key", "version"])
    op.create_table(
        "policy_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("anchor", sa.String(200), nullable=False),
        sa.Column("heading", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("embedding_space", sa.String(100), nullable=False),
        sa.Column("embedding", Vector(768) if postgres else sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["policy_documents.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("document_id", "ordinal"),
        sa.CheckConstraint("ordinal >= 0", name="ordinal"),
        sa.CheckConstraint(
            "embedding_space = 'gemini-embedding-001:768:unit-v1'", name="embedding_space"
        ),
    )
    op.create_index("ix_policy_chunks_document_id", "policy_chunks", ["document_id"])
    if postgres:
        # Exact cosine search is deliberate for this tiny corpus: no ANN recall loss.
        op.execute('ALTER TABLE public."policy_chunks" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE public."policy_chunks" FORCE ROW LEVEL SECURITY')
        op.execute('REVOKE ALL ON TABLE public."policy_chunks" FROM PUBLIC, anon, authenticated')
        op.execute(
            'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public."policy_chunks" TO service_role'
        )


def downgrade() -> None:
    op.drop_table("policy_chunks")
    with op.batch_alter_table("policy_documents") as batch:
        batch.drop_constraint("uq_policy_documents_source_key", type_="unique")
        for name, _ in reversed(DOCUMENT_COLUMNS):
            batch.drop_column(name)
    # vector may be shared by other applications; never drop the extension.
