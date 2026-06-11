# Call Transcriber

Pipeline locale Windows per trasformare registrazioni audio/video di call in una knowledge base Markdown pronta per Obsidian.

Il progetto prende file audio o video, estrae l'audio, lo trascrive con Groq Whisper, genera un riassunto Markdown fedele alla conversazione con un provider LLM modulare, archivia l'audio compresso e aggiorna indici navigabili con wikilink.

## Funzionalita'

- Watch automatico della cartella `da_processare/` (avviato automaticamente al login via Task Scheduler).
- Supporto a file audio e video comuni (`.m4a`, `.mp3`, `.wav`, `.mp4`, `.mkv`, `.mov`, ecc.).
- Trascrizione con Groq Whisper (`whisper-large-v3-turbo`).
- Riuso automatico di `trascrizione.txt` se una lavorazione precedente e' fallita dopo Whisper.
- Riassunti Markdown dettagliati, non generici, con:
  - frontmatter YAML per Obsidian;
  - titolo breve con nomi dei partecipanti;
  - sezioni granulari;
  - action item in tabella;
  - decisioni, dubbi, dipendenze e citazioni rilevanti.
- Provider LLM modulare via GitHub Copilot SDK:
  - modello summary principale `gemini-3.1-pro-preview`;
  - fallback summary, audit, classificazione task e Kanban `gpt-5.4-mini`.
- Classificazione automatica della call dentro una cartella task.
- Archiviazione automatica delle call piu' vecchie di N giorni.
- Archiviazione dei file audio/video originali in `completate/archivio`, con pulizia automatica dopo N giorni.
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
    archivio/              ← sorgenti audio/video originali processati
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
        copilot.py
  .env                     ← chiavi API (non tracciato)
  pyproject.toml
```

## Requisiti di sistema

- Python 3.11+
- `ffmpeg` e `ffprobe` nel PATH
- GitHub Copilot SDK autenticato tramite ambiente GitHub/Copilot

Installazione con `winget`:

```powershell
winget install Gyan.FFmpeg
```

## Installazione

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e .
```

Crea il file `.env` nella root del progetto:

```
GROQ_API_KEY=<la-tua-chiave-groq>
# opzionale, se l'SDK non trova gia' un'autenticazione GitHub/Copilot
COPILOT_GITHUB_TOKEN=<token-github>
```

Il provider LLM usa `github-copilot-sdk`. Il pacchetto espone il modulo Python `copilot`.

## Avvio automatico al login

Registra il watcher come task di Windows (una tantum):

```powershell
.\scripts\register_startup_task.ps1
```

Da questo momento `watch_calls.py` si avvia automaticamente ad ogni login. Lo script rimuove anche il vecchio task PowerShell `Call Automation Watcher`, se presente, per evitare watcher duplicati. Il watcher elabora anche i file gia' presenti in `da_processare/` all'avvio.

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

Senza `--keep-video`, il file sorgente viene spostato in `completate/archivio`. Con `--keep-video`, il video resta anche nel path originale e viene comunque copiato nell'archivio sorgenti.

## Configurazione

Tutti i parametri sono in `scripts/settings.py`:

```python
# Provider abilitati
ENABLED_PROVIDERS = ["copilot"]

# GitHub Copilot SDK
COPILOT_SUMMARY_MODEL = "gemini-3.1-pro-preview"
COPILOT_SUMMARY_FALLBACK_MODEL = "gpt-5.4-mini"
COPILOT_TASK_MODEL    = "gpt-5.4-mini"
COPILOT_LIGHT_MODEL   = "gpt-5.4-mini"
COPILOT_AUDIT_MODEL   = "gpt-5.4-mini"
COPILOT_REASONING_EFFORT = "medium"
COPILOT_SUMMARY_RETRIES = 2

# Groq / Trascrizione
GROQ_WHISPER_MODEL        = "whisper-large-v3-turbo"
TRANSCRIPTION_MAX_MB      = 19.0
TRANSCRIPTION_CHUNK_TARGET_MB = 18.0

# Pipeline
ARCHIVE_MAX_MB  = 19.0
ARCHIVE_DAYS    = 10
SOURCE_ARCHIVE_DAYS = 15

# Kanban
KANBAN_MAX_CARDS_PER_CALL = 4
```

## Provider LLM

`copilot` e' il provider predefinito e usa GitHub Copilot SDK in Python.

Il flusso qualitativo del riassunto resta composto da piu' passaggi:

1. draft del riassunto con `COPILOT_SUMMARY_MODEL`, con fallback a `COPILOT_SUMMARY_FALLBACK_MODEL` se la chiamata fallisce;
2. audit metadati, persone, sistemi, tag e frontmatter con `COPILOT_AUDIT_MODEL`;
3. audit decisioni, action item, dipendenze, domande aperte e citazioni con `COPILOT_AUDIT_MODEL`;
4. revisione finale del Markdown se gli audit segnalano correzioni;
5. validazione locale del formato e retry se il Markdown non e' valido.

## Output

Ogni call elaborata produce:

```text
completate/Task/<task>/<YYYY-MM-DD HH.mm - Titolo>/
  <Titolo>.md            ← riassunto Markdown
  trascrizione.txt        ← trascrizione completa riusabile in caso di retry
  audio_compresso.m4a    ← audio compresso sotto ARCHIVE_MAX_MB
```

Esempio di frontmatter generato:

```yaml
---
data: 2026-05-13
ora: "11:37"
task: "[[Italgas - MCP Server]]"
persone: [Daniela, Marco]
sistemi: [Databricks, GitHub Copilot SDK]
tags: [call, italgas, mcp-server]
---
```

## Flusso end-to-end

1. Il file viene copiato in `da_processare/`.
2. Il watcher rileva il file.
3. Attesa finche' dimensione e timestamp sono stabili.
4. `ffmpeg` estrae o converte l'audio in `audio.m4a`.
5. Se `trascrizione.txt` esiste gia' nella cartella della call, viene riusata; altrimenti Groq Whisper la produce con chunking automatico per file grandi.
6. GitHub Copilot SDK genera il riassunto Markdown e lo sottopone agli audit.
7. Titolo e frontmatter vengono normalizzati.
8. GitHub Copilot SDK classifica la call rispetto alle task esistenti.
9. La cartella viene spostata sotto `completate/Task/<task>/`.
10. L'audio viene compresso in `audio_compresso.m4a`.
11. I file intermedi vengono rimossi, mantenendo riassunto, trascrizione e audio compresso.
12. Il file sorgente audio/video viene spostato in `completate/archivio`.
13. I sorgenti archiviati piu' vecchi di `SOURCE_ARCHIVE_DAYS` vengono eliminati.
14. Gli indici Obsidian vengono rigenerati.
15. La Kanban del task viene aggiornata con le nuove card.

## Knowledge base Obsidian

La cartella `completate/` puo' essere aperta direttamente come vault Obsidian.

La pipeline genera automaticamente:

- `completate/README.md`: indice globale con task attive e ultime N call.
- `completate/Task/<task>/README.md`: indice della singola task con call attive e archivio.

Per rigenerare gli indici manualmente:

```powershell
.\.venv\Scripts\python.exe scripts\rebuild_indexes.py
```

Per archiviare anche le call vecchie prima di rigenerare gli indici:

```powershell
.\.venv\Scripts\python.exe scripts\rebuild_indexes.py --archive-old
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

**Provider Copilot non disponibile**: verifica che `github-copilot-sdk` sia installato nella venv e che l'autenticazione GitHub/Copilot sia disponibile.

**La call finisce nella root di `completate/Task/`**: il provider LLM non ha riconosciuto nessuna task. Verifica che le cartelle task abbiano nomi descrittivi.

**Gli indici non sono aggiornati**: esegui `rebuild_indexes.py` manualmente.
