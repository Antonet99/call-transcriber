# Call Transcriber

Pipeline locale per trasformare registrazioni audio/video di call in riassunti Markdown ordinati.

## Come funziona

1. Si inserisce una registrazione in `da_processare/`.
2. `watch_calls.ps1` intercetta il file.
3. `process_call.ps1` aspetta che il file sia stabile, estrae o converte l'audio con `ffmpeg` e crea una cartella temporanea di lavoro.
4. `transcribe_with_groq.ps1` invia l'audio a Groq Whisper e salva la trascrizione.
5. `summarize_with_claude.ps1` usa Claude CLI e `prompt_riassunto_call.md` per generare `riassunto.md`.
6. Lo script normalizza titolo e formato del riassunto, comprime l'audio sotto la soglia configurata e pulisce i file temporanei.
7. Claude classifica la call rispetto alle cartelle task esistenti e la sposta in `completate/Task/<task>/<data ora - titolo>/`.

## Struttura

```text
scripts/
  watch_calls.ps1
  process_call.ps1
  transcribe_with_groq.ps1
  summarize_with_claude.ps1
  prompt_riassunto_call.md
  run_watcher_task.ps1
  start_watcher.cmd
```

## Requisiti

- Windows PowerShell
- `ffmpeg` e `ffprobe`
- `curl.exe`
- variabile ambiente `GROQ_API_KEY`
- Claude CLI disponibile come comando `claude`

## Avvio

```bat
scripts\start_watcher.cmd
```

Le registrazioni, i riassunti generati e i log sono esclusi dal repository.
