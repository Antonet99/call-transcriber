"""Orchestratore principale della pipeline Call Transcriber.

Replica process_call.ps1: stabilizza, estrae audio, trascrive, riassume,
classifica, comprime, pulisce e rigenera gli indici.
"""
from __future__ import annotations

import argparse
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Literal

from rich.console import Console as _Console

from scripts.audio import ffmpeg as _ffmpeg
from scripts.llm import common as llm_common
from scripts.llm.providers.base import LlmProvider
from scripts.obsidian import frontmatter as fm
from scripts.obsidian import indexes as obs_indexes
import scripts.settings as _cfg

_con = _Console()

def _step(msg: str) -> None:
    _con.print(f"  [cyan]·[/cyan] {msg}")

def _ok(msg: str, elapsed: float = 0.0) -> None:
    t = f" [dim]{elapsed:.0f}s[/dim]" if elapsed >= 1 else ""
    _con.print(f"  [green]✓[/green] {msg}{t}")

def _warn(msg: str) -> None:
    _con.print(f"  [yellow]![/yellow] {msg}")

_UTF8 = "utf-8"
_AUDIO_EXT = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".webm", ".wma"}
_VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm"}

ProviderName = Literal["gemini", "claude", "codex"]


# ---------------------------------------------------------------------------
# Provider loading
# ---------------------------------------------------------------------------

def _load_provider(name: ProviderName) -> LlmProvider:
    if name not in _cfg.ENABLED_PROVIDERS:
        raise RuntimeError(
            f"Provider '{name}' disabilitato. Abilitarlo in scripts/settings.py per usarlo."
        )
    if name == "gemini":
        from scripts.llm.providers.gemini import GeminiProvider
        p = GeminiProvider()
    elif name == "claude":
        from scripts.llm.providers.claude import ClaudeProvider
        p = ClaudeProvider()
    elif name == "codex":
        from scripts.llm.providers.codex import CodexProvider
        p = CodexProvider()
    else:
        raise ValueError(f"Provider sconosciuto: {name}")

    if not p.is_available():
        raise RuntimeError(f"Provider LLM non disponibile: {name}")
    return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_name(name: str) -> str:
    invalid = set(r'\/:*?"<>|')
    safe = "".join('-' if c in invalid else c for c in name)
    return re.sub(r'\s+', ' ', safe).strip()


def _to_kebab(value: str) -> str:
    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', value.lower())).strip('-')


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    idx = 2
    while True:
        candidate = path.parent / f"{path.name} ({idx})"
        if not candidate.exists():
            return candidate
        idx += 1


def wait_stable(path: Path, stable_checks: int = 3, delay_seconds: int = 3) -> None:
    same = 0
    last_size = -1
    last_mtime = 0.0
    while same < stable_checks:
        time.sleep(delay_seconds)
        stat = path.stat()
        if stat.st_size == last_size and stat.st_mtime == last_mtime:
            same += 1
        else:
            same = 0
            last_size = stat.st_size
            last_mtime = stat.st_mtime


def _extract_title_from_summary(summary_path: Path) -> str:
    _GENERIC = {
        'contesto', 'decisioni prese', 'punti discussi', 'task e action item',
        'blocchi, dubbi o rischi', 'prossimi passi', 'passaggi ambigui da verificare',
    }
    _, body = fm.parse_frontmatter(summary_path.read_text(encoding=_UTF8))
    for line in body.splitlines():
        m = re.match(r'^##\s+(.+?)\s*$', line)
        if m:
            title = _safe_name(re.sub(r'[#*_`]', '', m.group(1)).strip())
            words = title.split()
            if len(words) > 6:
                title = " ".join(words[:6])
            if title and title.lower() not in _GENERIC:
                return title
    return ""


def _add_people_to_title(title: str, people: list[str]) -> str:
    """Prefissa al titolo i primi due partecipanti (primo nome, esclude MY_NAME)."""
    my_parts = {p.lower() for p in _cfg.MY_NAME.split()} if _cfg.MY_NAME else set()
    first_names: list[str] = []
    for p in people:
        if {w.lower() for w in p.split()} & my_parts:
            continue
        first_names.append(p.split()[0])
        if len(first_names) >= 2:
            break
    missing = [
        fn for fn in first_names
        if not re.search(r'(?i)(^|[\s,;-])' + re.escape(fn) + r'($|[\s,;-])', title)
    ]
    if not missing:
        return title
    return f"{', '.join(missing)}, {title}"


def _set_summary_title(summary_path: Path, title: str) -> None:
    content = summary_path.read_text(encoding=_UTF8).strip()
    fields, body = fm.parse_frontmatter(content)

    body = body.lstrip("\n")
    lines = body.splitlines()
    # rimuovi eventuale # riassunto e ## Titolo preesistenti
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and re.match(r'^#\s+', lines[0]):
        lines.pop(0)
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and re.match(r'^##\s+', lines[0]):
        lines.pop(0)

    new_body = f"# riassunto\n## {title}\n\n" + "\n".join(lines)
    fm.write_with_frontmatter(summary_path, fields, new_body)


def _set_summary_frontmatter(summary_path: Path, timestamp: datetime, task_name: str) -> None:
    fields, body = fm.parse_frontmatter(summary_path.read_text(encoding=_UTF8))
    fields.pop("data", None)
    fields.pop("ora", None)
    fields.pop("task", None)

    if "tags" not in fields:
        fields["tags"] = ["call"]

    ordered: dict = {
        "data": timestamp.strftime("%Y-%m-%d"),
        "ora": f'"{timestamp.strftime("%H:%M")}"',
    }
    if task_name:
        ordered["task"] = f'"[[{task_name}]]"'
    ordered.update(fields)
    fm.write_with_frontmatter(summary_path, ordered, body)


def _rename_summary(summary_path: Path, title: str) -> Path:
    target = summary_path.parent / (_safe_name(title) + ".md")
    if summary_path != target:
        summary_path.rename(target)
    return target


def _get_task_dir(
    root: Path,
    summary_path: Path,
    title: str,
    provider: LlmProvider,
    model: str,
) -> Path:
    task_root = root / "completate" / "Task"
    if not task_root.exists():
        return root / "completate"

    tasks = sorted([d for d in task_root.iterdir() if d.is_dir()], key=lambda d: d.name)
    if not tasks:
        return task_root

    summary = summary_path.read_text(encoding=_UTF8)
    task_names = [d.name for d in tasks]
    prompt = llm_common.build_task_prompt(task_names, title, summary)
    try:
        answer = provider.invoke_task_classification(prompt, model or provider.default_task_model())
        selected = llm_common.select_task(tasks, answer)
        if selected:
            return selected
    except Exception as exc:
        print(f"[warn] Classificazione task fallita: {exc}")
    return task_root


# ---------------------------------------------------------------------------
# Summary generation with fallback
# ---------------------------------------------------------------------------

def _invoke_summary(
    provider: LlmProvider,
    transcript_path: Path,
    output_path: Path,
    prompt_path: Path,
    model: str,
) -> None:
    transcript = transcript_path.read_text(encoding=_UTF8)
    prompt = llm_common.build_summary_prompt(prompt_path, transcript)
    raw = provider.invoke_summary(prompt, model or provider.default_summary_model())
    if not raw:
        raise RuntimeError("Il provider non ha restituito un riassunto.")
    clean = llm_common.clean_markdown(raw)
    if not clean:
        raise RuntimeError("Il provider non ha restituito Markdown valido.")
    llm_common.validate_summary(clean)
    output_path.write_text(clean, encoding=_UTF8)


def _summarize_with_fallback(
    root: Path,
    transcript_path: Path,
    output_path: Path,
    prompt_path: Path,
    provider_name: ProviderName,
    model: str,
    gemini_attempts: int,
    gemini_fallback_model: str,
) -> tuple[ProviderName, LlmProvider]:
    from scripts.llm.providers.gemini import GeminiCapacityError

    provider = _load_provider(provider_name)

    if provider_name != "gemini":
        _invoke_summary(provider, transcript_path, output_path, prompt_path, model)
        return provider_name, provider

    last_error: Exception | None = None
    for attempt in range(1, gemini_attempts + 1):
        try:
            _invoke_summary(provider, transcript_path, output_path, prompt_path, model)
            return "gemini", provider
        except GeminiCapacityError as exc:
            last_error = exc
            print(f"[warn] Gemini capacity exhausted, tentativo {attempt}/{gemini_attempts}.")

    if gemini_fallback_model:
        try:
            print(f"[warn] Fallback Gemini: provo {gemini_fallback_model}.")
            _invoke_summary(provider, transcript_path, output_path, prompt_path, gemini_fallback_model)
            return "gemini", provider
        except Exception:
            print(f"[warn] Fallback Gemini {gemini_fallback_model} fallito.")

    if "claude" not in _cfg.ENABLED_PROVIDERS:
        raise last_error or RuntimeError(
            "Tutti i tentativi Gemini hanno fallito e Claude è disabilitato in settings.py."
        )

    print("[warn] Fallback su Claude.")
    claude_provider = _load_provider("claude")
    _invoke_summary(claude_provider, transcript_path, output_path, prompt_path, claude_provider.default_summary_model())
    if not output_path.exists():
        raise last_error or RuntimeError("Summary generation fallita.")
    return "claude", claude_provider


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def process(
    input_path: Path,
    root: Path | None = None,
    keep_video: bool = False,
    archive_max_mb: float | None = None,
    provider_name: ProviderName | None = None,
    summary_model: str = "",
    task_model: str = "",
    gemini_capacity_attempts: int | None = None,
    gemini_fallback_model: str | None = None,
) -> dict:
    if archive_max_mb is None:
        archive_max_mb = _cfg.ARCHIVE_MAX_MB
    if provider_name is None:
        provider_name = _cfg.ENABLED_PROVIDERS[0] if _cfg.ENABLED_PROVIDERS else "gemini"
    if gemini_capacity_attempts is None:
        gemini_capacity_attempts = _cfg.GEMINI_CAPACITY_ATTEMPTS
    if gemini_fallback_model is None:
        gemini_fallback_model = _cfg.GEMINI_FALLBACK_MODEL
    if root is None:
        root = Path(__file__).parent.parent

    resolved = input_path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"File non trovato: {input_path}")

    t0 = time.perf_counter()
    size_mb = resolved.stat().st_size / 1_000_000
    _con.rule(f"[bold]Call Transcriber[/bold]  [dim]{resolved.name} ({size_mb:.1f} MB)[/dim]")

    with _con.status("  Attesa stabilizzazione file..."):
        wait_stable(resolved)
    _ok("File stabile")

    ext = resolved.suffix.lower()
    if ext not in _AUDIO_EXT and ext not in _VIDEO_EXT:
        raise ValueError(f"Estensione non supportata: {ext}")

    is_video = ext in _VIDEO_EXT
    timestamp = datetime.fromtimestamp(resolved.stat().st_mtime)
    call_name = f"{timestamp.strftime('%Y-%m-%d %H.%M')} - {_safe_name(resolved.stem)}"
    call_dir = root / "completate" / call_name
    call_dir.mkdir(parents=True, exist_ok=True)

    audio_path = call_dir / "audio.m4a"
    archive_audio_path = call_dir / "audio_compresso.m4a"

    if is_video:
        with _con.status("  Estrazione audio dal video..."):
            _ffmpeg.extract_audio(resolved, audio_path)
        _ok("Audio estratto")
    elif ext == ".m4a":
        shutil.copy2(resolved, call_dir / f"audio_originale{ext}")
        shutil.copy2(resolved, audio_path)
    else:
        with _con.status(f"  Conversione {ext} → m4a..."):
            shutil.copy2(resolved, call_dir / f"audio_originale{ext}")
            _ffmpeg.convert_to_m4a(resolved, audio_path)
        _ok(f"Audio convertito")

    transcript_path = call_dir / "trascrizione.txt"
    summary_path = call_dir / "riassunto.md"
    prompt_path = Path(__file__).parent / "prompt_riassunto_call.md"

    from scripts.transcribe_with_groq import transcribe
    t = time.perf_counter()
    with _con.status(f"  Trascrizione Whisper ({_cfg.GROQ_WHISPER_MODEL})..."):
        transcript_text = transcribe(audio_path, transcript_path)
    _ok(f"Trascrizione completata ({len(transcript_text)} caratteri)", time.perf_counter() - t)

    t = time.perf_counter()
    with _con.status(f"  Generazione riassunto ({provider_name})..."):
        active_provider_name, active_provider = _summarize_with_fallback(
            root=root,
            transcript_path=transcript_path,
            output_path=summary_path,
            prompt_path=prompt_path,
            provider_name=provider_name,
            model=summary_model,
            gemini_attempts=gemini_capacity_attempts,
            gemini_fallback_model=gemini_fallback_model,
        )
    if active_provider_name != provider_name:
        _ok(f"Riassunto generato (fallback su {active_provider_name})", time.perf_counter() - t)
    else:
        _ok(f"Riassunto generato", time.perf_counter() - t)

    context_title = _extract_title_from_summary(summary_path)
    if not context_title:
        context_title = _safe_name(resolved.stem)
    people = fm.get_people(summary_path)
    context_title = _add_people_to_title(context_title, people)
    _step(f"Titolo: [italic]{context_title}[/italic]")

    _set_summary_title(summary_path, context_title)

    final_call_name = f"{timestamp.strftime('%Y-%m-%d %H.%M')} - {context_title}"
    active_task_model = task_model if active_provider_name == provider_name else ""
    t = time.perf_counter()
    with _con.status("  Classificazione task..."):
        task_dir = _get_task_dir(root, summary_path, context_title, active_provider, active_task_model)
    task_name = task_dir.name if (task_dir.parent == root / "completate" / "Task") else ""
    _ok(f"Task: [bold]{task_dir.name}[/bold]", time.perf_counter() - t)

    final_call_dir = _unique_path(task_dir / final_call_name)
    call_dir.rename(final_call_dir)
    call_dir = final_call_dir
    audio_path = call_dir / "audio.m4a"
    archive_audio_path = call_dir / "audio_compresso.m4a"
    summary_path = call_dir / "riassunto.md"

    _set_summary_frontmatter(summary_path, timestamp, task_name)

    title_from_dir = re.sub(r'^\d{4}-\d{2}-\d{2}\s+\d{2}\.\d{2}\s+-\s+', '', call_dir.name).strip()
    summary_path = _rename_summary(summary_path, title_from_dir)

    t = time.perf_counter()
    with _con.status(f"  Compressione audio (max {archive_max_mb} MB)..."):
        duration = _ffmpeg.get_duration(audio_path)
        _ffmpeg.compress_audio(audio_path, archive_audio_path, archive_max_mb, duration)
    compressed_mb = archive_audio_path.stat().st_size / 1_000_000
    _ok(f"Audio compresso: {compressed_mb:.1f} MB", time.perf_counter() - t)

    keep_names = {archive_audio_path.name, summary_path.name}
    for item in list(call_dir.iterdir()):
        if item.name not in keep_names:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

    if is_video and not keep_video:
        resolved.unlink(missing_ok=True)
    elif not is_video:
        resolved.unlink(missing_ok=True)

    with _con.status("  Archiviazione call vecchie..."):
        from scripts.archive_old_calls import archive as archive_old_calls
        archive_result = archive_old_calls(root)
    if archive_result["archived"]:
        _ok(f"Call archiviate: {archive_result['archived']}")

    with _con.status("  Rigenerazione indici Obsidian..."):
        obs_indexes.rebuild(root)
    _ok("Indici aggiornati")

    kanban_ok = False
    with _con.status("  Aggiornamento Kanban..."):
        try:
            from scripts.update_project_kanban import update_from_summary
            if task_dir.exists():
                update_from_summary(summary_path, task_dir, active_provider_name)
            kanban_ok = True
        except Exception as exc:
            _warn(f"Kanban non aggiornata (non bloccante): {exc}")
    if kanban_ok:
        _ok("Kanban aggiornata")

    _con.rule(f"[bold green]Completato[/bold green]  [dim]{call_dir.name}[/dim]  [dim]{time.perf_counter() - t0:.0f}s totali[/dim]")

    return {
        "call_directory": str(call_dir),
        "task_directory": str(task_dir),
        "audio": str(archive_audio_path),
        "summary": str(summary_path),
        "provider": active_provider_name,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Processa una registrazione audio/video.")
    parser.add_argument("--input-path", required=True, type=Path)
    parser.add_argument("--root-path", type=Path, default=None)
    parser.add_argument("--keep-video", action="store_true")
    parser.add_argument("--archive-max-mb", type=float, default=19.0)
    parser.add_argument("--provider", default="gemini", choices=["gemini", "claude", "codex"])
    parser.add_argument("--summary-model", default="")
    parser.add_argument("--task-model", default="")
    parser.add_argument("--gemini-capacity-attempts", type=int, default=2)
    parser.add_argument("--gemini-fallback-model", default="gemini-3-pro-preview")
    args = parser.parse_args()

    result = process(
        input_path=args.input_path,
        root=args.root_path,
        keep_video=args.keep_video,
        archive_max_mb=args.archive_max_mb,
        provider_name=args.provider,
        summary_model=args.summary_model,
        task_model=args.task_model,
        gemini_capacity_attempts=args.gemini_capacity_attempts,
        gemini_fallback_model=args.gemini_fallback_model,
    )
    for k, v in result.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
