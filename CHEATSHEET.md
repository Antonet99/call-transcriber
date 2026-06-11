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

# Mantieni il video anche nel path originale dopo la lavorazione
.\.venv\Scripts\python.exe scripts\process_call.py `
  --input-path ".\da_processare\riunione.mp4" `
  --keep-video

# Forza il provider
.\.venv\Scripts\python.exe scripts\process_call.py `
  --input-path ".\da_processare\registrazione.m4a" `
  --provider copilot

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

### Forza il provider

```powershell
.\.venv\Scripts\python.exe scripts\update_project_kanban.py `
  --all `
  --task-directory ".\completate\Task\Italgas - MCP Server" `
  --provider copilot
```

---

## Indici Obsidian

### Rigenera tutti gli indici README.md

```powershell
.\.venv\Scripts\python.exe scripts\rebuild_indexes.py
```

### Rigenera gli indici e archivia le call vecchie

```powershell
.\.venv\Scripts\python.exe scripts\rebuild_indexes.py --archive-old
```

---

## Archivio

I file audio/video originali processati vengono salvati in `completate\archivio`.
La pulizia automatica rimuove i sorgenti piu' vecchi di `SOURCE_ARCHIVE_DAYS`.

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

Durante la pipeline completa, `trascrizione.txt` viene salvata nella cartella della call.
Se un retry trova gia' una trascrizione non vuota, salta la chiamata Groq Whisper.

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
| `ENABLED_PROVIDERS` | `["copilot"]` | Provider attivo |
| `COPILOT_SUMMARY_MODEL` | `gemini-3.1-pro-preview` | Modello riassunto principale |
| `COPILOT_SUMMARY_FALLBACK_MODEL` | `gpt-5.4-mini` | Fallback riassunto se il principale fallisce |
| `COPILOT_TASK_MODEL` | `gpt-5.4-mini` | Modello classificazione task |
| `COPILOT_LIGHT_MODEL` | `gpt-5.4-mini` | Modello Kanban |
| `COPILOT_AUDIT_MODEL` | `gpt-5.4-mini` | Modello audit riassunto |
| `COPILOT_REASONING_EFFORT` | `medium` | Effort predefinito |
| `COPILOT_SUMMARY_RETRIES` | `2` | Retry se il Markdown non valida |
| `GROQ_WHISPER_MODEL` | `whisper-large-v3-turbo` | Modello trascrizione |
| `ARCHIVE_MAX_MB` | `19.0` | Soglia compressione audio |
| `ARCHIVE_DAYS` | `10` | Giorni prima dell'archiviazione |
| `SOURCE_ARCHIVE_DAYS` | `15` | Giorni prima di eliminare i sorgenti audio/video archiviati |
| `KANBAN_MAX_CARDS_PER_CALL` | `4` | Max card per call |
