from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Read from environment variables (case-insensitive), e.g. DATABASE_URL."""

    database_url: str
    redis_url: str
    api_key: SecretStr = Field(min_length=1)
    host_ttl_secs: int = Field(default=30, gt=0)
    alert_engine_enabled: bool = True
    alert_interval_secs: float = Field(default=5.0, gt=0)
