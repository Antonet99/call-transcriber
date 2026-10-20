"""Explicit, bounded provider probes and configuration preflight."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import time
import wave

from scripts import settings, user_settings
from scripts.filesystem import atomic_write_text

TTL_SECONDS = 24 * 3600
PROBE_TIMEOUT = 60
PROBE_BUDGET = 0.05
_PROMPT = "Test sintetico: rispondi soltanto OK. Non leggere file, usare rete o modificare dati."


class ConfigurationNotReady(RuntimeError):
    pass


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _stamp(path: Path):
    try:
        stat = path.stat()
        return [str(path), stat.st_mtime_ns, stat.st_size]
    except OSError:
        return [str(path), None]


def context() -> str:
    from scripts.llm.providers.claude import ClaudeProvider
    provider = ClaudeProvider()
    executable = provider._resolve_executable()
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    files = [home / ".credentials.json", home / "settings.json", Path.home() / ".claude.json",
             provider._cwd / ".claude" / "settings.json", provider._cwd / ".claude" / "settings.local.json"]
    if executable:
        files.append(Path(executable))
    environment = {key: value for key, value in os.environ.items()
                   if key.startswith(("ANTHROPIC_", "CLAUDE_", "GROQ_", "AWS_", "GOOGLE_"))}
    return _digest({"environment": environment, "files": [_stamp(p) for p in files],
                    "cwd": str(provider._cwd), "executable": executable,
                    "provider": settings.LLM_PROVIDER,
                    "tools": getattr(settings, "CLAUDE_TOOLS", None),
                    "turns": getattr(settings, "CLAUDE_MAX_TURNS", 3)})


def fingerprint(values: dict) -> str:
    return _digest({"version": 1, "settings": values, "context": context()})


def report_path() -> Path:
    return user_settings.path().with_name("verification.json")


def cached(values: dict) -> dict | None:
    try:
        report = json.loads(report_path().read_text(encoding="utf-8"))
        age = time.time() - report["checked_at"]
        valid_results = isinstance(report.get("results"), list) and all(
            isinstance(item, dict) and item.get("status") in {"verificato", "fallito", "parziale", "non verificato"}
            and isinstance(item.get("phase"), str) and isinstance(item.get("message"), str)
            for item in report["results"])
        if valid_results and report["fingerprint"] == fingerprint(values) and 0 <= age < TTL_SECONDS:
            return report
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _result(phase: str, status: str, message: str) -> dict:
    return {"phase": phase, "status": status, "message": message}


def local_checks(values: dict) -> list[dict]:
    try:
        user_settings.validate(values)
        if set(values) != set(user_settings.FIELDS):
            raise ValueError("Configurazione incompleta.")
    except ValueError as exc:
        return [_result("Locale", "fallito", str(exc))]
    results = [_result("Locale", "verificato", "Tipi, identificativi e intervalli corretti; disponibilita' da provare.")]
    # Older Haiku models do not support the effort parameter.
    for role in ("SUMMARY", "TASK", "LIGHT", "SUBAGENT"):
        model = values[f"CLAUDE_{role}_MODEL"]
        if model in {"claude-3-haiku-20240307", "claude-3-5-haiku-20241022", "claude-haiku-4-5", "claude-haiku-4-5-20251001"}:
            results.append(_result(role, "fallito", "Questo modello non supporta effort; scegliere un modello compatibile. Nessuna correzione automatica."))
        if model in {"claude-opus-4-6", "claude-sonnet-4-6"} and values[f"CLAUDE_{role}_EFFORT"] == "xhigh":
            results.append(_result(role, "fallito", "Questo modello non supporta xhigh; scegliere un effort supportato."))
    return results


def _error(exc: Exception) -> str:
    text = str(exc).lower()
    if isinstance(exc, subprocess.TimeoutExpired) or "timeout" in text or "timed out" in text:
        return "Timeout: riprovare esplicitamente o controllare la connessione."
    for words, message in [(("401", "403", "authentication", "api key", "login", "unauthorized"), "Autenticazione/accesso: controllare l'account del provider."),
                           (("effort",), "Effort rifiutato: scegliere un livello supportato dal modello e dalla CLI."),
                           (("model", "404", "not_found"), "Modello inesistente, non accessibile o non supportato: controllare l'identificativo presso il provider."),
                           (("budget",), "Budget esaurito nella prova; il limite della prova e' 0.05 USD per invocazione."),
                           (("429", "rate limit"), "Limite del provider raggiunto; riprovare piu' tardi."),
                           (("network", "connection", "connect"), "Connessione al provider non riuscita.")]:
        if any(word in text for word in words):
            return message
    return "Prova fallita: controllare CLI, parametri e disponibilita' del provider. Dettagli sensibili omessi."


def _claude_probe(provider, model: str, effort: str, agents: str = "") -> dict:
    prompt = _PROMPT
    if agents:
        prompt = ("Test sintetico: call di prova, nessun partecipante o azione. "
                  "Invoca entrambi call-metadata-auditor e call-action-auditor su questo solo testo, "
                  "senza accesso a file o altri dati. Poi rispondi OK.")
    provider._call(prompt, model, effort, agents, max_turns=min(getattr(settings, "CLAUDE_MAX_TURNS", 3), 3))
    if "effort_ignored" in provider.last_warnings:
        return _result(model + " / " + effort, "fallito", "La CLI segnala effort ignorato o non supportato. Controllare modello, effort e versione CLI.")
    if "fallback" in provider.last_warnings:
        return _result(model + " / " + effort, "fallito", "La CLI segnala fallback: il modello richiesto non e' attestato.")
    usage = provider.last_response.get("modelUsage", {})
    expected = {model}
    if agents:
        expected.update(agent["model"] for agent in json.loads(agents).values())
    if isinstance(usage, dict) and usage and (set(usage) - expected):
        return _result(model + " / " + effort, "fallito", "Risposta con modello diverso/fallback. Controllare alias e disponibilita'; nessuna sostituzione automatica.")
    if not isinstance(usage, dict) or model not in usage:
        message = "Risposta ricevuta; modello effettivo ed effort non attestabili dal JSON della CLI."
    else:
        message = "Modello presente nei consumi CLI; effort effettivo non attestabile dal JSON."
    if agents:
        message += " Definizioni revisori inviate; esecuzione, modello ed effort dei singoli revisori non attestabili."
    return _result(model + " / " + effort, "parziale", message)


def verify(values: dict, *, audio: bool = False, provider=None, groq_factory=None) -> dict:
    """The caller must obtain explicit consent before calling this function."""
    results = local_checks(values)
    report = {"checked_at": time.time(), "fingerprint": fingerprint(values), "results": results}
    if any(item["status"] == "fallito" for item in results):
        return report
    if settings.LLM_PROVIDER != "claude":
        results.append(_result("Claude", "non verificato", "Verifica disponibile solo per il provider Claude CLI."))
        return report
    from scripts.llm.providers.claude import ClaudeProvider, _summary_agents_json
    provider = provider or ClaudeProvider()
    limited = dict(values)
    limited["CLAUDE_TIMEOUT_SECONDS"] = min(values["CLAUDE_TIMEOUT_SECONDS"], PROBE_TIMEOUT)
    limited["CLAUDE_MAX_BUDGET_USD"] = min(values["CLAUDE_MAX_BUDGET_USD"], PROBE_BUDGET)
    pairs = {}
    for role in ("SUMMARY", "TASK", "LIGHT", "SUBAGENT"):
        pair = (values[f"CLAUDE_{role}_MODEL"], values[f"CLAUDE_{role}_EFFORT"])
        pairs.setdefault(pair, []).append(role)
    with user_settings.use(limited):
        stopped = False
        for (model, effort), roles in pairs.items():
            if stopped:
                item = _result(model + " / " + effort, "non verificato", "Prove Claude interrotte dopo il primo errore; correggere e riavviare esplicitamente.")
            else:
                try:
                    item = _claude_probe(provider, model, effort)
                except Exception as exc:
                    item = _result(model + " / " + effort, "fallito", _error(exc))
                stopped = item["status"] == "fallito"
            item["phase"] = ", ".join(roles) + " · " + item["phase"]
            results.append(item)
        if not any(item["status"] == "fallito" for item in results):
            try:
                item = _claude_probe(provider, values["CLAUDE_SUMMARY_MODEL"], values["CLAUDE_SUMMARY_EFFORT"], _summary_agents_json())
                item["phase"] = "Riassunto con revisori"
            except Exception as exc:
                item = _result("Riassunto con revisori", "fallito", _error(exc))
            results.append(item)
        else:
            results.append(_result("Riassunto con revisori", "non verificato", "Correggere prima gli errori Claude."))
    try:
        if groq_factory is None:
            from groq import Groq
            groq_factory = Groq
        with groq_factory(timeout=min(values["CLAUDE_TIMEOUT_SECONDS"], 30), max_retries=0) as client:
            model = values["GROQ_WHISPER_MODEL"]
            models = client.models.list()
            if model not in {entry.id for entry in models.data}:
                results.append(_result("Groq catalogo", "fallito", "Modello assente dal catalogo accessibile all'account."))
            else:
                results.append(_result("Groq catalogo", "verificato", "Credenziali accettate e modello presente; endpoint audio da provare."))
                if audio:
                    sample = io.BytesIO()
                    with wave.open(sample, "wb") as wav:
                        wav.setnchannels(1)
                        wav.setsampwidth(2)
                        wav.setframerate(16000)
                        wav.writeframes(b"\0\0" * 16000)
                    response = client.audio.transcriptions.create(file=("synthetic.wav", sample.getvalue()), model=model, response_format="text", temperature=0.0)
                    if not isinstance(response, str):
                        raise ValueError("Risposta audio inattesa")
                    results.append(_result("Groq audio", "verificato", "Endpoint audio accetta 1 secondo di silenzio sintetico; qualita' su parlato non verificata."))
                else:
                    results.append(_result("Groq audio", "non verificato", "Catalogo controllato; nessuna trascrizione di prova eseguita."))
    except Exception as exc:
        results.append(_result("Groq", "fallito", _error(exc)))
    return report


def describe(report: dict | None) -> str:
    if not report:
        return "Non verificata, scaduta o configurazione/account cambiati."
    return "\n".join(f"{item['phase']}: {item['status']} · {item['message']}" for item in report["results"])


def _overview(report: dict | None) -> str:
    if not report:
        return describe(None)
    statuses = [item["status"] for item in report["results"]]
    return "Ultima prova: " + ", ".join(f"{status}: {statuses.count(status)}" for status in dict.fromkeys(statuses))


def show_report(report: dict, *, title: str, message: str = "") -> None:
    from scripts.terminal import Choice, select_item
    colors = {"verificato": "#86efac", "fallito": "#fb7185", "parziale": "#fbbf24", "non verificato": "#94a3b8"}
    choices = [Choice(index, f"{item['phase']}: {item['status']}", style=colors[item["status"]])
               for index, item in enumerate(report["results"])]
    while True:
        selected = select_item(title, choices, message="Seleziona una fase per i dettagli. Esc torna indietro.\n" + message)
        if selected is None:
            return
        item = report["results"][selected]
        select_item(item["phase"], [], message=f"{item['status']}\n\n{item['message']}\n\nEsc torna agli esiti.")


def show_verification(values: dict, *, draft: bool = False) -> None:
    from scripts.terminal import Choice, phase, select_item
    message = ("BOZZA attualmente visibile (non salva le preferenze)." if draft else "Configurazione effettiva della pipeline/checkpoint.")
    message += ("\nProve a pagamento: massimo 5 invocazioni Claude CLI, ciascuna fino a 60s, 3 turni e 0.05 USD "
                "(o limiti configurati inferiori), nessun retry. Il budget CLI e' una stima, puo' essere superato. "
                "Groq: 1 richiesta catalogo, opzionalmente 1 secondo di silenzio sintetico (fatturazione minima provider). "
                "Stesso ambiente e directory della pipeline: si applicano anche hook e configurazioni CLI locali.\n"
                "Esiti validi al massimo 24 ore; account CLI non sempre identificabile.\n\n")
    action = select_item("Verifica configurazione", [Choice("catalog", "Avvia prove Claude + catalogo Groq"),
                                                   Choice("audio", "Avvia prove Claude + catalogo e audio Groq")],
                         message=message + _overview(cached(values)))
    if action not in {"catalog", "audio"}:
        return
    with phase("Verifica configurazione · dati sintetici"):
        report = verify(values, audio=action == "audio")
    try:
        target = report_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, json.dumps(report, indent=2) + "\n")
        persistence = ""
    except OSError:
        persistence = "\nEsiti non salvati; controllare i permessi delle preferenze."
    show_report(report, title="Esiti verifica · bozza" if draft else "Esiti verifica", message=persistence)


def require_ready(values: dict, *, allow_unverified: bool = False) -> None:
    results = local_checks(values)
    if any(item["status"] == "fallito" for item in results):
        raise ConfigurationNotReady(describe({"results": results}))
    if allow_unverified:
        return
    from scripts.terminal import Choice, interactive, select_item
    while True:
        report = cached(values)
        if report and report["results"] and all(item["status"] == "verificato" for item in report["results"]):
            return
        message = (_overview(report) + "\n\nElaborazione sospesa prima di Whisper/Claude sulla registrazione. "
                   "Verifica o correggi da Impostazioni. Procedere comunque accetta il rischio di errore e consumo; vale solo per questa esecuzione.")
        if not interactive():
            raise ConfigurationNotReady(message + "\n" + describe(report) + "\nIn modalita' manuale: --allow-unverified-config per procedere esplicitamente.")
        choices = [Choice("verify", "Verifica questa configurazione"),
                   Choice("proceed", "Procedi comunque per questa registrazione", style="#fbbf24")]
        if report:
            choices.insert(0, Choice("details", "Mostra esiti precedenti"))
        action = select_item("Configurazione da verificare", choices, message=message)
        if action == "verify":
            show_verification(values)
        elif action == "details":
            show_report(report, title="Configurazione effettiva · esiti precedenti")
        elif action == "proceed":
            return
        else:
            raise ConfigurationNotReady("Elaborazione annullata: correggere/verificare la configurazione da Impostazioni.")


def pending_config(saved: dict, current: dict, *, use_current: bool = False) -> dict:
    if saved == current:
        return saved
    if use_current:
        return current
    from scripts.terminal import Choice, interactive, select_item
    if interactive():
        action = select_item("Checkpoint fermo alla verifica", [Choice("saved", "Mantieni configurazione del checkpoint"),
                                                               Choice("current", "Applica le preferenze correnti a questo checkpoint")],
                             message="La precedente verifica ha bloccato la call prima di elaborare l'audio. "
                             "Puoi applicare esplicitamente le preferenze corrette e verificarle prima di iniziare.")
        if action == "current":
            return current
        if action == "saved":
            return saved
        raise ConfigurationNotReady("Ripresa annullata; nessuna elaborazione avviata.")
    raise ConfigurationNotReady("Checkpoint fermo alla verifica con preferenze diverse. "
                                "Usare --use-current-config per applicare esplicitamente le preferenze corrette prima di riprovare.")
