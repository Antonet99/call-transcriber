"""Validated personal preferences, independent of credentials and vault data."""
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re

from scripts.filesystem import atomic_write_text


FIELDS = {
    "CLAUDE_SUMMARY_MODEL": ("Riassunto - modello", str),
    "CLAUDE_SUMMARY_EFFORT": ("Riassunto - ragionamento", str),
    "CLAUDE_TASK_MODEL": ("Classificazione - modello", str),
    "CLAUDE_TASK_EFFORT": ("Classificazione - ragionamento", str),
    "CLAUDE_LIGHT_MODEL": ("Operazioni leggere - modello", str),
    "CLAUDE_LIGHT_EFFORT": ("Operazioni leggere - ragionamento", str),
    "CLAUDE_SUBAGENT_MODEL": ("Revisori - modello", str),
    "CLAUDE_SUBAGENT_EFFORT": ("Revisori - ragionamento", str),
    "GROQ_WHISPER_MODEL": ("Trascrizione - modello Groq", str),
    "CLAUDE_SUMMARY_RETRIES": ("Avanzate - tentativi aggiuntivi", int),
    "CLAUDE_TIMEOUT_SECONDS": ("Avanzate - timeout Claude (secondi)", float),
    "CLAUDE_MAX_BUDGET_USD": ("Avanzate - limite per invocazione Claude (USD)", float),
}
EFFORTS = ("low", "medium", "high", "xhigh", "max")


def path() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "CallTranscriber" / "settings.json"


def validate(values: dict) -> dict:
    if not isinstance(values, dict) or values.keys() - FIELDS.keys():
        raise ValueError("Configurazione non valida o impostazioni sconosciute.")
    for key, value in values.items():
        kind = FIELDS[key][1]
        if kind is str:
            if not isinstance(value, str) or not value or len(value) > 128:
                raise ValueError(f"{FIELDS[key][0]}: valore non valido.")
            if key.endswith("_EFFORT"):
                if value not in EFFORTS:
                    raise ValueError("Livello di ragionamento non valido.")
            elif not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", value):
                raise ValueError("Identificativo modello non valido.")
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{FIELDS[key][0]}: numero non valido.")
        elif kind is int and (not isinstance(value, int) or not 0 <= value <= 10):
            raise ValueError("Tentativi aggiuntivi: usare un intero da 0 a 10.")
        elif kind is float and not 0 < value <= (86400 if "TIMEOUT" in key else 1000):
            raise ValueError(f"{FIELDS[key][0]}: valore fuori intervallo.")
    return dict(values)


def load(config_path: Path | None = None) -> dict:
    target = config_path or path()
    if not target.exists():
        return {}
    try:
        return validate(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Preferenze non caricate da {target}: {exc}") from exc


def save(values: dict, config_path: Path | None = None) -> None:
    values = validate(values)
    target = config_path or path()
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, json.dumps(values, indent=2) + "\n")


def snapshot() -> dict:
    from scripts import settings
    return {key: getattr(settings, key) for key in FIELDS}


@contextmanager
def use(values: dict):
    from scripts import settings
    values = validate(values)
    previous = {key: getattr(settings, key) for key in values}
    try:
        for key, value in values.items():
            setattr(settings, key, value)
        yield
    finally:
        for key, value in previous.items():
            setattr(settings, key, value)


def show_menu() -> None:
    from scripts import settings
    from scripts.terminal import Choice, input_line, select_item

    draft = snapshot()
    message = settings.USER_SETTINGS_ERROR or str(path())
    while True:
        choices = [Choice(key, f"{label}: {draft[key]}") for key, (label, _) in FIELDS.items()]
        choices += [Choice("verify", "Verifica configurazione · bozza attuale", style="#67e8f9"),
                    Choice("save", "Salva modifiche", style="#86efac"),
                    Choice("reset", "Ripristina valori predefiniti", style="#fbbf24")]
        action = select_item("Impostazioni", choices, message=message)
        if action is None:
            if draft != snapshot():
                if select_item("Modifiche non salvate", [Choice("discard", "Scarta modifiche")]) != "discard":
                    continue
            return
        if action == "verify":
            from scripts.config_verification import show_verification
            show_verification(draft, draft=True)
            message = "Controllo della bozza terminato. Le modifiche alle preferenze richiedono Salva modifiche."
            continue
        if action == "reset":
            if select_item("Ripristina impostazioni", [Choice("reset", "Conferma ripristino nel menu")]) == "reset":
                draft = dict(settings.USER_DEFAULTS)
            continue
        if action == "save":
            try:
                save(draft)
            except (OSError, ValueError) as exc:
                message = f"Salvataggio non riuscito: {exc}"
                continue
            for key, value in draft.items():
                setattr(settings, key, value)
            settings.USER_SETTINGS_ERROR = ""
            return
        label, kind = FIELDS[action]
        if action.endswith("_EFFORT"):
            value = select_item(label, [Choice(level, level) for level in EFFORTS])
        else:
            value = input_line(label, str(draft[action]))
        if value is None:
            continue
        try:
            candidate = value.strip() if kind is str else kind(value)
            validate({action: candidate})
        except (ValueError, TypeError) as exc:
            message = f"Valore non valido: {exc}"
            continue
        draft[action] = candidate
        message = "Modifiche da salvare. La disponibilita' del modello dipende dal provider."
