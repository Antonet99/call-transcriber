# Cheatsheet comandi Call Transcriber

Tutti i comandi vanno eseguiti dalla root del progetto:

```powershell
cd "C:\Users\ABAIO\OneDrive - ICONSULTING S.p.A\Desktop\call-transcriber"
```

Nota: il codice vive in `Desktop\call-transcriber`, mentre il vault Obsidian
vero vive in `Desktop\Call\vault`. I comandi sotto vanno eseguiti dalla root
del codice; gli input e gli output operativi stanno sotto `VAULT_ROOT`.

Alias consigliato per non ripetere il path del Python ogni volta:

```powershell
Set-Alias py .\.venv\Scripts\python.exe
```

---

## Watcher

### Modalita' 1 — Terminale visibile (display live)

Ferma il task scheduler se attivo, poi avvia il watcher in un terminale.
Il menu usa la grafica Rich esistente e attende una scelta per ogni registrazione.

```powershell
Stop-ScheduledTask -TaskName 'CallWatcher'
.\scripts\start_watcher.cmd
```

### Modalita' 2 — Avvio al login nella sessione interattiva

Il task avvia il launcher nella sessione utente. Il menu richiede una console interattiva: se manca stdin interattivo, la coda viene conservata e il watcher esce senza elaborare. In quel caso aprire `scripts\start_watcher.cmd` in una console. I file arrivati mentre il watcher è spento vengono rilevati all'avvio successivo.

```powershell
# Registra il task (una tantum, o dopo ogni modifica)
.\scripts\register_startup_task.ps1

# Avvia subito nella sessione utente
Start-ScheduledTask -TaskName 'CallWatcher'

# Ferma
Stop-ScheduledTask -TaskName 'CallWatcher'

# Rimuovi il task
Unregister-ScheduledTask -TaskName 'CallWatcher' -Confirm:$false

# Verifica stato
Get-ScheduledTask -TaskName 'CallWatcher' | Select-Object TaskName, State
```

### Scelte del menu

- `1`: trascrivi e riassumi con la pipeline attuale.
- `2`: salva l'originale e una scheda Markdown; titolo modificabile e progetto scelto manualmente (`0` = Senza progetto, `Esc` = torna al menu). Nessuna estrazione audio o chiamata a provider.
- `3`: sposta nel cestino recuperabile per 30 giorni, senza ulteriore conferma. La pulizia definitiva avviene durante la manutenzione del watcher attivo.
- `Q`: mostra la coda e permette di selezionare una registrazione pronta.
- `R`: riprende un'operazione interrotta; nessun retry automatico.
- `Ctrl+C`: esce mantenendo coda e decisioni.

Durante l'elaborazione il watcher continua a rilevare altri file. Il menu torna al termine. Al riavvio vengono recuperati ordine e scelte; una registrazione senza scelta resta in attesa.

Anche le registrazioni salvate con `2` seguono l'archiviazione dopo 15 giorni e la rimozione del video: senza audio estratto, dopo tale rimozione resta solo la scheda Markdown.

### Monitoraggio del log in tempo reale

```powershell
Get-Content -Path "..\Call\vault\logs\watcher.log" -Wait -Tail 30
```

---

## Processa una singola call

```powershell
# Caso base
.\.venv\Scripts\python.exe scripts\process_call.py `
  --input-path "..\Call\vault\da_processare\registrazione.m4a"

# Il video viene conservato automaticamente nella cartella finale della call
.\.venv\Scripts\python.exe scripts\process_call.py `
  --input-path "..\Call\vault\da_processare\riunione.mp4"

# Soglia audio personalizzata
.\.venv\Scripts\python.exe scripts\process_call.py `
  --input-path "..\Call\vault\da_processare\registrazione.m4a" `
  --archive-max-mb 25
```

---

## Kanban

### Aggiorna da un singolo riassunto

```powershell
.\.venv\Scripts\python.exe scripts\update_project_kanban.py `
  --summary-path "..\Call\vault\completate\Task\Italgas - MCP Server\2026-05-21 12.03 - Titolo\Titolo.md" `
  --task-directory "..\Call\vault\completate\Task\Italgas - MCP Server"
```

### Aggiorna da tutte le call di una task

```powershell
.\.venv\Scripts\python.exe scripts\update_project_kanban.py `
  --all `
  --task-directory "..\Call\vault\completate\Task\Italgas - MCP Server"
```

### Includi anche le call archiviate

```powershell
.\.venv\Scripts\python.exe scripts\update_project_kanban.py `
  --all --include-archive `
  --task-directory "..\Call\vault\completate\Task\Italgas - MCP Server"
```

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

I video vengono salvati nella cartella della call con il titolo del riassunto. Dopo 15 giorni la call viene archiviata e il video viene eliminato; riassunto, trascrizione e audio compresso restano disponibili. `completate\archivio` contiene solo eventuali sorgenti legacy non associati.

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
  --audio-path "..\Call\vault\da_processare\registrazione.m4a"

# Output in un path specifico
.\.venv\Scripts\python.exe scripts\transcribe_with_groq.py `
  --audio-path "..\Call\vault\da_processare\registrazione.m4a" `
  --output-path ".\trascrizione.txt"
```

---

## Log

```powershell
# Leggi il log del watcher in tempo reale
Get-Content -Path "..\Call\vault\logs\watcher.log" -Wait -Tail 30
```

---

## Configurazione

Tutti i parametri si trovano in `scripts\settings.py`:

| Parametro | Default | Descrizione |
|---|---|---|
| `CLAUDE_SUMMARY_MODEL` | `claude-sonnet-5` | Modello Claude per il riassunto |
| `CLAUDE_SUMMARY_EFFORT` | `medium` | Effort Claude per il riassunto |
| `CLAUDE_TASK_MODEL` | `claude-sonnet-5` | Modello Claude per la classificazione task |
| `CLAUDE_LIGHT_MODEL` | `claude-sonnet-5` | Modello Claude per il Kanban |
| `CLAUDE_SUBAGENT_MODEL` | `claude-sonnet-5` | Modello subagent di audit |
| `CLAUDE_SUBAGENT_EFFORT` | `medium` | Effort subagent di audit |
| `CLAUDE_SUMMARY_RETRIES` | `2` | Retry se il Markdown non valida |
| `GROQ_WHISPER_MODEL` | `whisper-large-v3-turbo` | Modello trascrizione |
| `ARCHIVE_MAX_MB` | `19.0` | Soglia compressione audio |
| `ARCHIVE_DAYS` | `15` | Giorni prima dell'archiviazione e della rimozione del video |
| `SOURCE_ARCHIVE_DAYS` | `15` | Soglia di pulizia dei sorgenti audio legacy nell'archivio generale |
| `KANBAN_MAX_CARDS_PER_CALL` | `4` | Max card per call |
