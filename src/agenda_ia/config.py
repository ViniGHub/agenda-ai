from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "dados"
DEFAULT_DB = DATA_DIR / "agenda.db"
SAMPLE_CALENDAR = ROOT / "exemplos" / "agenda_exemplo.ics"

# Every date is shown and compared in São Paulo time.
TZ = ZoneInfo("America/Sao_Paulo")

# Small multilingual model (384 dimensions) that runs on CPU and handles Portuguese.
# E5 models expect the prefixes "query: " and "passage: ".
EMBEDDING_MODEL = "intfloat/multilingual-e5-small"
EMBEDDING_DIM = 384

# Free tier on Groq. The key comes from the GROQ_API_KEY variable (or the .env file).
LLM_MODEL = "openai/gpt-oss-120b"
