from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    # Target site (SauceDemo: public demo store)
    base_url: str = Field(default="https://www.saucedemo.com/")
    
    # Demo credentials (publicly provided by SauceDemo)
    username: str = Field(default="standard_user")
    password: str = Field(default="secret_sauce")

    # Timeouts (ms)
    nav_timeout_ms: int = 20000
    action_timeout_ms: int = 10000

    # Playwright
    headless: bool = True

    # class Config:
        # env_file = ".env"

settings = Settings()