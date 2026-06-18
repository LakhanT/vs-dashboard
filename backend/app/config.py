from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "VS Dashboard API"
    debug: bool = True
    database_url: str = "sqlite:///./vs_dashboard.db"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    excel_import_path: str = ""
    # OHLC history: yahoo (recommended). Live LTP: fyers (recommended) with yahoo fallback.
    ohlc_data_source: str = "yahoo"
    live_price_source: str = "fyers"  # fyers | yahoo
    primary_data_source: str = "yahoo"  # legacy alias for ohlc_data_source
    fyers_client_id: str = ""
    fyers_secret_key: str = ""
    fyers_access_token: str = ""
    fyers_fy_id: str = ""
    fyers_pin: str = ""
    fyers_totp_key: str = ""
    fyers_credentials_file: str = ""
    fyers_redirect_uri: str = "http://127.0.0.1:5000/callback"
    live_price_enabled: bool = True
    live_price_interval_sec: int = 3
    live_price_batch_size: int = 100  # max symbols per watch subscription

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
