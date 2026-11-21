"""Apply the platform database migrations (alembic upgrade head)."""

from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def upgrade(database_url: str, revision: str = "head") -> None:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    # configparser interpolation: a literal % must be doubled
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, revision)
