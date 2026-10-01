from datetime import date

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./recovery_os.db"
    cors_origins: str = "http://localhost:3000"
    demo_today: str = ""
    default_role: str = "approver"  # role assumed when no X-Role header is sent (demo only)
    allow_reseed: bool = False  # exposes POST /api/admin/reseed for e2e test resets

    def today(self) -> date:
        return date.fromisoformat(self.demo_today) if self.demo_today else date.today()


settings = Settings()

DEMO_DISCLAIMER = (
    "Illustrative demo data only. Built from public operating context and synthetic assumptions; "
    "not representative of Revise Robotics' internal data, customers, economics, or processes."
)


def today() -> date:
    return settings.today()
