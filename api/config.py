from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Read from environment variables (case-insensitive), e.g. DATABASE_URL."""

    database_url: str
    redis_url: str
