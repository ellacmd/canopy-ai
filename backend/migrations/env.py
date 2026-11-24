from logging.config import fileConfig

from alembic import context

from canopy import models  # noqa: F401 — register all mapped tables
from canopy.config import Settings
from canopy.db import Base, make_engine

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata
settings = Settings()

if context.is_offline_mode():
    context.configure(
        url=settings.database_url.get_secret_value(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = make_engine(settings, runtime_role=False)
    with engine.connect() as connection:
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()
        if connection.dialect.name == "sqlite":
            if connection.exec_driver_sql("PRAGMA foreign_key_check").all():
                raise RuntimeError("Migration left invalid foreign keys")
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    engine.dispose()
