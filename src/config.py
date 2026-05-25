import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL: str = os.environ["DATABASE_URL"]
COMPANIES_HOUSE_API_KEY: str = os.environ["COMPANIES_HOUSE_API_KEY"]
APP_ENV: str = os.getenv("APP_ENV", "production")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
RM_NAMES: list[str] = [
    name.strip()
    for name in os.getenv("RM_NAMES", "").split(",")
    if name.strip()
]
