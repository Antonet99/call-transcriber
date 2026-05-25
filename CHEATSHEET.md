# Cheatsheet comandi Call Transcriber

Tutti i comandi vanno eseguiti dalla root del progetto:

```powershell
cd "C:\Users\ABAIO\OneDrive - ICONSULTING S.p.A\Desktop\Call"
```

Alias consigliato per non ripetere il path del Python ogni volta:

```powershell
Set-Alias py .\.venv\Scripts\python.exe
```

---

## Watcher

### Modalita' 1 — Terminale visibile (display live)

Ferma il task scheduler se attivo, poi avvia il watcher in un terminale.
Ogni call processata mostrera' il display rich con spinner in quella finestra.

```powershell
Stop-ScheduledTask -TaskName 'CallWatcher'
.\.venv\Scripts\python.exe scripts\watch_calls.py
```

### Modalita' 2 — Background automatico (avvio al login)

Il watcher gira in background senza finestra. L'output viene scritto nel log.

```powershell
# Registra il task (una tantum, o dopo ogni modifica)
.\scripts\register_startup_task.ps1

# Avvia subito senza fare logout
Start-ScheduledTask -TaskName 'CallWatcher'

# Ferma
Stop-ScheduledTask -TaskName 'CallWatcher'

# Rimuovi il task
Unregister-ScheduledTask -TaskName 'CallWatcher' -Confirm:$false

# Verifica stato
Get-ScheduledTask -TaskName 'CallWatcher' | Select-Object TaskName, State
```

### Monitoraggio background in tempo reale

```powershell
Get-Content -Path ".\logs\watcher.log" -Wait -Tail 30
```

---

## Processa una singola call

```powershell
# Caso base
.\.venv\Scripts\python.exe scripts\process_call.py `
  --input-path ".\da_processare\registrazione.m4a"

# Mantieni il video originale dopo la lavorazione
.\.venv\Scripts\python.exe scripts\process_call.py `
  --input-path ".\da_processare\riunione.mp4" `
  --keep-video

# Forza un provider specifico
.\.venv\Scripts\python.exe scripts\process_call.py `
  --input-path ".\da_processare\registrazione.m4a" `
  --provider claude

# Soglia audio personalizzata
.\.venv\Scripts\python.exe scripts\process_call.py `
  --input-path ".\da_processare\registrazione.m4a" `
  --archive-max-mb 25
```

---

## Kanban

### Aggiorna da un singolo riassunto

```powershell
.\.venv\Scripts\python.exe scripts\update_project_kanban.py `
  --summary-path ".\completate\Task\Italgas - MCP Server\2026-05-21 12.03 - Titolo\Titolo.md" `
  --task-directory ".\completate\Task\Italgas - MCP Server"
```

### Aggiorna da tutte le call di una task

```powershell
.\.venv\Scripts\python.exe scripts\update_project_kanban.py `
  --all `
  --task-directory ".\completate\Task\Italgas - MCP Server"
```

### Includi anche le call archiviate

```powershell
.\.venv\Scripts\python.exe scripts\update_project_kanban.py `
  --all --include-archive `
  --task-directory ".\completate\Task\Italgas - MCP Server"
```

### Forza un provider specifico

```powershell
.\.venv\Scripts\python.exe scripts\update_project_kanban.py `
  --all `
  --task-directory ".\completate\Task\Italgas - MCP Server" `
  --provider claude
```

---

## Indici Obsidian

### Rigenera tutti gli indici README.md

```powershell
.\.venv\Scripts\python.exe scripts\rebuild_indexes.py
```

---

## Archivio

### Archivia manualmente le call vecchie (usa ARCHIVE_DAYS da settings.py)

```powershell
.\.venv\Scripts\python.exe scripts\archive_old_calls.py
```

### Archivia con soglia personalizzata

```powershell
.\.venv\Scripts\python.exe scripts\archive_old_calls.py --days 30
```

---

## Trascrizione standalone

```powershell
# Trascrivi un file audio senza processare tutta la pipeline
.\.venv\Scripts\python.exe scripts\transcribe_with_groq.py `
  --audio-path ".\da_processare\registrazione.m4a"

# Output in un path specifico
.\.venv\Scripts\python.exe scripts\transcribe_with_groq.py `
  --audio-path ".\da_processare\registrazione.m4a" `
  --output-path ".\trascrizione.txt"
```

---

## Log

```powershell
# Leggi il log del watcher in tempo reale
Get-Content -Path ".\logs\watcher.log" -Wait -Tail 30
```

---

## Configurazione

Tutti i parametri si trovano in `scripts\settings.py`:

| Parametro | Default | Descrizione |
|---|---|---|
| `ENABLED_PROVIDERS` | `["gemini", "claude"]` | Provider attivi e ordine di fallback |
| `GEMINI_SUMMARY_MODEL` | `gemini-3.1-pro-preview` | Modello riassunto Gemini |
| `GEMINI_TASK_MODEL` | `gemini-3-flash-preview` | Modello classificazione task |
| `GEMINI_FALLBACK_MODEL` | `gemini-3-flash-preview` | Fallback se quota esaurita |
| `GEMINI_CAPACITY_ATTEMPTS` | `2` | Tentativi prima del fallback |
| `CLAUDE_SUMMARY_MODEL` | `claude-sonnet-4-6` | Modello riassunto Claude |
| `CLAUDE_SUMMARY_EFFORT` | `medium` | Effort riassunto Claude |
| `CLAUDE_SUBAGENT_MODEL` | `claude-haiku-4-5` | Modello subagent revisori |
| `GROQ_WHISPER_MODEL` | `whisper-large-v3-turbo` | Modello trascrizione |
| `ARCHIVE_MAX_MB` | `19.0` | Soglia compressione audio |
| `ARCHIVE_DAYS` | `10` | Giorni prima dell'archiviazione |
| `KANBAN_MAX_CARDS_PER_CALL` | `5` | Max card per call |
