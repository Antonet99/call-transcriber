# ---------------------------------------------------------------------------
# Configurazione pipeline Call Transcriber
# ---------------------------------------------------------------------------
import os as _os
from pathlib import Path as _Path

from dotenv import load_dotenv as _load_dotenv

_load_dotenv(_Path(__file__).parent.parent / ".env", override=True)

# Root del vault Obsidian (completate/, da_processare/, .obsidian/).
# In sviluppo locale (repo unificato) coincide con la root del codice.
# Dopo la separazione codice/vault, impostare VAULT_ROOT nel .env.
VAULT_ROOT: _Path = _Path(
    _os.environ.get("VAULT_ROOT", str(_Path(__file__).parent.parent))
)

# Il tuo nome completo: viene escluso dai partecipanti nel titolo delle call
MY_NAME: str = "Antonio Baio"

# ---------------------------------------------------------------------------
# Claude CLI  (effort: low | medium | high | xhigh | max)
# ---------------------------------------------------------------------------
CLAUDE_SUMMARY_MODEL: str = "claude-sonnet-5"
CLAUDE_SUMMARY_EFFORT: str = "medium"

CLAUDE_TASK_MODEL: str = "claude-sonnet-5"
CLAUDE_TASK_EFFORT: str = "medium"

CLAUDE_LIGHT_MODEL: str = "claude-sonnet-5"
CLAUDE_LIGHT_EFFORT: str = "medium"

# Subagent usati come revisori interni durante la generazione del riassunto
CLAUDE_SUBAGENT_MODEL: str = "claude-sonnet-5"
CLAUDE_SUBAGENT_EFFORT: str = "medium"
CLAUDE_SUMMARY_RETRIES: int = 2

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
ARCHIVE_DAYS: int = 15
SOURCE_ARCHIVE_DAYS: int = 15
UNASSIGNED_CALLS_DIR_NAME: str = "Senza progetto"

# ---------------------------------------------------------------------------
# LLM prompts
# ---------------------------------------------------------------------------
TASK_PROMPT_SUMMARY_TRUNCATE: int = 5000
TASK_PROMPT_TRANSCRIPT_TRUNCATE: int = 12000
# Scoring classificazione: una keyword forte vale piu' di un tag.
TASK_KEYWORD_SCORE: int = 3
TASK_TAG_SCORE: int = 1
TASK_SCORE_MIN_TOTAL: int = 3
TASK_SCORE_MIN_MARGIN: int = 3
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
