import os

from dotenv import load_dotenv

load_dotenv()

RIOT_API_KEY = os.getenv("RIOT_API_KEY", "")
PLATFORM = os.getenv("PLATFORM", "EUW1").upper()
SYNC_QUEUES = [int(q) for q in os.getenv("SYNC_QUEUES", "420,440,450").split(",") if q.strip()]
DB_PATH = os.getenv("DB_PATH", "data/guild.db")

REGION_BY_PLATFORM = {
    "NA1": "americas", "BR1": "americas", "LA1": "americas", "LA2": "americas",
    "KR": "asia", "JP1": "asia",
    "EUW1": "europe", "EUN1": "europe", "TR1": "europe", "RU": "europe", "ME1": "europe",
    "OC1": "sea", "PH2": "sea", "SG2": "sea", "TH2": "sea", "TW2": "sea", "VN2": "sea",
}
REGION = REGION_BY_PLATFORM.get(PLATFORM, "europe")
