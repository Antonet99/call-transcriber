# ---------------------------------------------------------------------------
# Configurazione pipeline Call Transcriber
# ---------------------------------------------------------------------------
from pathlib import Path as _Path

from dotenv import load_dotenv as _load_dotenv

_load_dotenv(_Path(__file__).parent.parent / ".env")

# Provider abilitati.
# Valori disponibili: "copilot"
ENABLED_PROVIDERS: list[str] = [
    "copilot",
]

# Il tuo nome completo: viene escluso dai partecipanti nel titolo delle call
MY_NAME: str = "Antonio Baio"

# ---------------------------------------------------------------------------
# GitHub Copilot SDK
# ---------------------------------------------------------------------------
COPILOT_SUMMARY_MODEL: str = "gemini-3.1-pro-preview"
COPILOT_SUMMARY_FALLBACK_MODEL: str = "gpt-5.4-mini"
COPILOT_TASK_MODEL: str = "gpt-5.4-mini"
COPILOT_LIGHT_MODEL: str = "gpt-5.4-mini"
COPILOT_AUDIT_MODEL: str = "gpt-5.4-mini"

COPILOT_REASONING_EFFORT: str = "medium"
COPILOT_LIGHT_REASONING_EFFORT: str = "medium"

COPILOT_SUMMARY_RETRIES: int = 2
COPILOT_SUMMARY_TIMEOUT_SECONDS: int = 900
COPILOT_LIGHT_TIMEOUT_SECONDS: int = 300

# ---------------------------------------------------------------------------
# Groq / Trascrizione
# ---------------------------------------------------------------------------
GROQ_WHISPER_MODEL: str = "whisper-large-v3-turbo"
TRANSCRIPTION_MAX_MB: float = 19.0
TRANSCRIPTION_CHUNK_TARGET_MB: float = 18.0

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
ARCHIVE_MAX_MB: float = 19.0
ARCHIVE_DAYS: int = 10
SOURCE_ARCHIVE_DAYS: int = 15

# ---------------------------------------------------------------------------
# LLM prompts
# ---------------------------------------------------------------------------
TASK_PROMPT_SUMMARY_TRUNCATE: int = 5000
KANBAN_PROMPT_SUMMARY_TRUNCATE: int = 6000

# ---------------------------------------------------------------------------
# Indici Obsidian
# ---------------------------------------------------------------------------
INDEX_TITLE_MAX_WORDS: int = 6
INDEX_LATEST_CALLS_COUNT: int = 10

# ---------------------------------------------------------------------------
# Kanban
# ---------------------------------------------------------------------------
KANBAN_MAX_CARDS_PER_CALL: int = 4
KANBAN_DEDUP_LENGTH: int = 60
