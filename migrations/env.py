import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, make_url, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Tests pass the URL through config.attributes; everything else uses the environment.
url = config.attributes.get("database_url") or os.environ["DATABASE_URL"]

# NullPool: no connection outlives the migration, so tests can use the database as a template.
engine = create_engine(make_url(url).set(drivername="postgresql+psycopg"), poolclass=pool.NullPool)

with engine.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
