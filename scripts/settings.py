# ---------------------------------------------------------------------------
# Configurazione pipeline Call Transcriber
# ---------------------------------------------------------------------------
from pathlib import Path as _Path

from dotenv import load_dotenv as _load_dotenv

_load_dotenv(_Path(__file__).parent.parent / ".env")

# Provider abilitati, in ordine di preferenza (il primo è il principale, gli altri sono fallback).
# Valori disponibili: "gemini", "claude"
ENABLED_PROVIDERS: list[str] = [
    "gemini",
    "claude",
]

# Il tuo nome completo: viene escluso dai partecipanti nel titolo delle call
MY_NAME: str = "Antonio Baio"

# ---------------------------------------------------------------------------
# Gemini CLI
# ---------------------------------------------------------------------------
GEMINI_SUMMARY_MODEL: str = "gemini-3.1-pro-preview"
GEMINI_TASK_MODEL: str = "gemini-3-flash-preview"
GEMINI_LIGHT_MODEL: str = "gemini-3-flash-preview"

# Modello di fallback se il principale esaurisce la quota
GEMINI_FALLBACK_MODEL: str = "gemini-3-flash-preview"

# Tentativi con il modello principale prima di passare al fallback
GEMINI_CAPACITY_ATTEMPTS: int = 2

# ---------------------------------------------------------------------------
# Claude CLI  (effort: low | medium | high | xhigh | max)
# ---------------------------------------------------------------------------
CLAUDE_SUMMARY_MODEL: str = "claude-sonnet-4-6"
CLAUDE_SUMMARY_EFFORT: str = "medium"

CLAUDE_TASK_MODEL: str = "claude-sonnet-4-6"
CLAUDE_TASK_EFFORT: str = "low"

CLAUDE_LIGHT_MODEL: str = "claude-haiku-4-5"
CLAUDE_LIGHT_EFFORT: str = "low"

# Subagent usati come revisori interni durante la generazione del riassunto
CLAUDE_SUBAGENT_MODEL: str = "claude-haiku-4-5"
CLAUDE_SUBAGENT_EFFORT: str = "high"

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
