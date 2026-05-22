# Call Transcriber

Pipeline locale Windows per trasformare registrazioni audio/video di call in una knowledge base Markdown pronta per Obsidian.

Il progetto prende file audio o video, estrae l'audio, lo trascrive con Groq Whisper, genera un riassunto Markdown fedele alla conversazione con un provider LLM modulare, archivia l'audio compresso e aggiorna indici navigabili con wikilink.

## Funzionalita'

- Watch automatico della cartella `da_processare/` (avviato automaticamente al login via Task Scheduler).
- Supporto a file audio e video comuni (`.m4a`, `.mp3`, `.wav`, `.mp4`, `.mkv`, `.mov`, ecc.).
- Trascrizione con Groq Whisper (`whisper-large-v3-turbo`).
- Riassunti Markdown dettagliati, non generici, con:
  - frontmatter YAML per Obsidian;
  - titolo breve con nomi dei partecipanti;
  - sezioni granulari;
  - action item in tabella;
  - decisioni, dubbi, dipendenze e citazioni rilevanti.
- Provider LLM modulari via CLI (nessuna API key LLM necessaria):
  - Gemini come provider principale;
  - Claude come provider di fallback.
- Classificazione automatica della call dentro una cartella task.
- Archiviazione automatica delle call piu' vecchie di N giorni.
- Aggiornamento automatico della Kanban di progetto con card estratte dal riassunto.
- Compressione dell'audio archiviato sotto una soglia configurabile.
- Indici Obsidian auto-generati:
  - indice globale;
  - indice per task con sezione archivio.

## Architettura

```text
Call/
  da_processare/           ← copia qui i file da processare
  completate/
    README.md              ← indice globale auto-generato
    Task/
      <nome task>/
        README.md          ← indice task auto-generato
        Kanban.md          ← kanban auto-aggiornata
        <YYYY-MM-DD HH.mm - titolo>/
          <titolo call>.md
          audio_compresso.m4a
        archivio/          ← call piu' vecchie di ARCHIVE_DAYS
  logs/
  scripts/
    process_call.py        ← orchestratore principale
    watch_calls.py         ← watcher cartella
    transcribe_with_groq.py
    rebuild_indexes.py
    archive_old_calls.py
    update_project_kanban.py
    settings.py            ← configurazione centralizzata
    prompt_riassunto_call.md
    register_startup_task.ps1
    audio/
      ffmpeg.py
    llm/
      common.py
      providers/
        base.py
        gemini.py
        claude.py
  .env                     ← chiavi API (non tracciato)
  pyproject.toml
```

## Requisiti di sistema

- Python 3.11+
- `ffmpeg` e `ffprobe` nel PATH
- Gemini CLI (`gemini`) autenticato
- Claude CLI (`claude`) autenticato
- `ripgrep` (`rg`) nel PATH (consigliato per performance Gemini CLI)

Installazione con `winget`:

```powershell
winget install Gyan.FFmpeg
winget install BurntSushi.ripgrep.MSVC
```

## Installazione

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e .
```

Crea il file `.env` nella root del progetto:

```
GROQ_API_KEY=<la-tua-chiave-groq>
```

Gemini e Claude vengono invocati tramite CLI; non serve nessuna API key aggiuntiva.

## Avvio automatico al login

Registra il watcher come task di Windows (una tantum):

```powershell
.\scripts\register_startup_task.ps1
```

Da questo momento `watch_calls.py` si avvia automaticamente ad ogni login. Il watcher elabora anche i file gia' presenti in `da_processare/` all'avvio.

Comandi utili:

```powershell
Start-ScheduledTask -TaskName 'CallWatcher'   # avvia subito
Stop-ScheduledTask  -TaskName 'CallWatcher'   # ferma
Unregister-ScheduledTask -TaskName 'CallWatcher' -Confirm:$false  # rimuovi
```

## Avvio manuale del watcher

```powershell
.\.venv\Scripts\python.exe scripts\watch_calls.py
```

## Uso manuale (singola call)

```powershell
.\.venv\Scripts\python.exe scripts\process_call.py --input-path .\da_processare\call.m4a
```

Mantenere il video originale dopo la lavorazione:

```powershell
.\.venv\Scripts\python.exe scripts\process_call.py --input-path .\da_processare\call.mp4 --keep-video
```

## Configurazione

Tutti i parametri sono in `scripts/settings.py`:

```python
# Provider abilitati (ordine = priorita' / fallback)
ENABLED_PROVIDERS = ["gemini", "claude"]

# Modelli Gemini CLI
GEMINI_SUMMARY_MODEL  = "gemini-3.1-pro-preview"
GEMINI_TASK_MODEL     = "gemini-3-flash-preview"
GEMINI_FALLBACK_MODEL = "gemini-3-flash-preview"
GEMINI_CAPACITY_ATTEMPTS = 2

# Modelli e effort Claude CLI
CLAUDE_SUMMARY_MODEL  = "claude-sonnet-4-6"
CLAUDE_SUMMARY_EFFORT = "medium"         # low | medium | high | xhigh | max
CLAUDE_TASK_MODEL     = "claude-sonnet-4-6"
CLAUDE_TASK_EFFORT    = "low"
CLAUDE_LIGHT_MODEL    = "claude-haiku-4-5"
CLAUDE_LIGHT_EFFORT   = "low"
CLAUDE_SUBAGENT_MODEL = "claude-haiku-4-5"
CLAUDE_SUBAGENT_EFFORT = "high"

# Groq / Trascrizione
GROQ_WHISPER_MODEL        = "whisper-large-v3-turbo"
TRANSCRIPTION_MAX_MB      = 19.0
TRANSCRIPTION_CHUNK_TARGET_MB = 18.0

# Pipeline
ARCHIVE_MAX_MB  = 19.0
ARCHIVE_DAYS    = 10

# Kanban
KANBAN_MAX_CARDS_PER_CALL = 5
```

## Provider LLM

Gemini e' il provider predefinito. Il flusso di fallback e' il seguente:

1. `GEMINI_CAPACITY_ATTEMPTS` tentativi con `GEMINI_SUMMARY_MODEL`
2. 1 tentativo con `GEMINI_FALLBACK_MODEL`
3. Fallback a Claude (`CLAUDE_SUMMARY_MODEL` con effort `CLAUDE_SUMMARY_EFFORT`)

Il provider Gemini usa i subagent locali `.gemini/agents/` se presenti.

Il provider Claude passa al volo due subagent per la revisione del riassunto:

- `call-metadata-auditor` (`claude-haiku-4-5`, effort `high`): controlla persone, sistemi, tag e frontmatter.
- `call-action-auditor` (`claude-haiku-4-5`, effort `high`): controlla decisioni, action item, dipendenze e citazioni.

## Output

Ogni call elaborata produce:

```text
completate/Task/<task>/<YYYY-MM-DD HH.mm - Titolo>/
  <Titolo>.md            ← riassunto Markdown
  audio_compresso.m4a    ← audio compresso sotto ARCHIVE_MAX_MB
```

Esempio di frontmatter generato:

```yaml
---
data: 2026-05-13
ora: "11:37"
task: "[[Italgas - MCP Server]]"
persone: [Daniela, Marco]
sistemi: [Databricks, Gemini CLI]
tags: [call, italgas, mcp-server]
---
```

## Flusso end-to-end

1. Il file viene copiato in `da_processare/`.
2. Il watcher rileva il file.
3. Attesa finche' dimensione e timestamp sono stabili.
4. `ffmpeg` estrae o converte l'audio in `audio.m4a`.
5. Groq Whisper produce `trascrizione.txt` (con chunking automatico per file grandi).
6. Gemini CLI genera il riassunto Markdown (con fallback automatico a Claude).
7. Titolo e frontmatter vengono normalizzati.
8. Gemini CLI classifica la call rispetto alle task esistenti.
9. La cartella viene spostata sotto `completate/Task/<task>/`.
10. L'audio viene compresso in `audio_compresso.m4a`.
11. I file intermedi vengono rimossi; il file sorgente viene cancellato.
12. Gli indici Obsidian vengono rigenerati.
13. La Kanban del task viene aggiornata con le nuove card.

## Knowledge base Obsidian

La cartella `completate/` puo' essere aperta direttamente come vault Obsidian.

La pipeline genera automaticamente:

- `completate/README.md`: indice globale con task attive e ultime N call.
- `completate/Task/<task>/README.md`: indice della singola task con call attive e archivio.

Per rigenerare gli indici manualmente:

```powershell
.\.venv\Scripts\python.exe scripts\rebuild_indexes.py
```

## Aggiornamento manuale Kanban

```powershell
.\.venv\Scripts\python.exe scripts\update_project_kanban.py `
  --summary-path ".\completate\Task\<task>\<call>\<titolo>.md" `
  --task-directory ".\completate\Task\<task>"
```

## Sviluppo

### Aggiungere un provider LLM

1. Crea `scripts/llm/providers/<nome>.py` che estende `LlmProvider` (vedi `base.py`).
2. Implementa `is_available`, `default_summary_model`, `default_task_model`, `invoke_summary`, `invoke_task_classification`, `invoke_light`.
3. Aggiungi il nome a `ENABLED_PROVIDERS` in `settings.py`.
4. Registra il caricamento in `_load_provider()` dentro `process_call.py` e `update_project_kanban.py`.

### File esclusi dal repository

Registrazioni, audio compressi, trascrizioni, riassunti, log, vault generati e `.env` sono esclusi da Git.

## Troubleshooting

**`GROQ_API_KEY` mancante**: aggiungi la chiave al file `.env`.

**`ffmpeg` non trovato**: `winget install Gyan.FFmpeg` e riavvia il terminale.

**`gemini` o `claude` non trovati**: verifica che le CLI siano installate e nel PATH.

**Ripgrep non trovato**: Gemini CLI stampa `Ripgrep is not available. Falling back to GrepTool.` se `rg` non e' nel PATH. `winget install BurntSushi.ripgrep.MSVC`.

**La call finisce nella root di `completate/Task/`**: il provider LLM non ha riconosciuto nessuna task. Verifica che le cartelle task abbiano nomi descrittivi.

**Gli indici non sono aggiornati**: esegui `rebuild_indexes.py` manualmente.
