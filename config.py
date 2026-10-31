import os
from dotenv import load_dotenv

load_dotenv()


def _bool(name, default):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes")


DB_PATH = os.getenv("DB_PATH", "crapbin.db")
JSON_DIR = os.getenv("JSON_DIR", "calls_json")

# Shared secret appended to the webhook URL in Bolna: /webhook/bolna?token=...
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "")

# LLM Configuration (supports "gemini" default or "openai" for GPT-4.1 mini)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

# Messaging. DRY_RUN=true logs messages to the DB without sending anything.
DRY_RUN = _bool("DRY_RUN", True)
SEND_WINDOW_START = int(os.getenv("SEND_WINDOW_START", "9"))   # IST hour, inclusive
SEND_WINDOW_END = int(os.getenv("SEND_WINDOW_END", "20"))      # IST hour, exclusive
REENGAGE_COOLDOWN_DAYS = int(os.getenv("REENGAGE_COOLDOWN_DAYS", "7"))

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v21.0")
WA_TEMPLATE_INTERESTED = os.getenv("WA_TEMPLATE_INTERESTED", "crapbin_interested_followup")
WA_TEMPLATE_REENGAGE = os.getenv("WA_TEMPLATE_REENGAGE", "crapbin_reengage")
WA_TEMPLATE_LANG = os.getenv("WA_TEMPLATE_LANG", "en")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)
COORDINATOR_EMAIL = os.getenv("COORDINATOR_EMAIL", "")

CRAPBIN_CONTACT_PHONE = os.getenv("CRAPBIN_CONTACT_PHONE", "")

PORT = int(os.getenv("PORT", "8000"))
FOLLOWUP_INTERVAL_MIN = int(os.getenv("FOLLOWUP_INTERVAL_MIN", "10"))
