# Call Transcriber

Pipeline locale Windows per trasformare registrazioni audio/video di call in una knowledge base Markdown pronta per Obsidian.

Il progetto prende file audio o video, estrae l'audio, lo trascrive con Groq Whisper, genera un riassunto Markdown fedele alla conversazione con un provider LLM modulare, archivia l'audio compresso e aggiorna indici navigabili con wikilink.

## Funzionalita'

- Watch automatico della cartella `da_processare/`.
- Supporto a file audio e video comuni (`.m4a`, `.mp3`, `.wav`, `.mp4`, `.mkv`, `.mov`, ecc.).
- Trascrizione con Groq Whisper (`whisper-large-v3-turbo`).
- Riassunti Markdown dettagliati, non generici, con:
  - frontmatter YAML per Obsidian;
  - titolo breve;
  - sezioni granulari;
  - action item in tabella;
  - decisioni, dubbi, dipendenze e citazioni rilevanti.
- Provider LLM modulari:
  - Gemini come default;
  - Claude come provider alternativo;
  - Codex predisposto come provider futuro.
- Classificazione automatica della call dentro una cartella task.
- Compressione dell'audio archiviato sotto una soglia configurabile.
- Indici Obsidian auto-generati:
  - indice globale;
  - indice per task;
  - wikilink relativi.

## Architettura

```text
Call/
  da_processare/
  completate/
    README.md
    Task/
      <nome task>/
        README.md
        <YYYY-MM-DD HH.mm - titolo>/
          <titolo call>.md
          audio_compresso.m4a
  logs/
  scripts/
    watch_calls.ps1
    process_call.ps1
    transcribe_with_groq.ps1
    prompt_riassunto_call.md
    rebuild_indexes.ps1
    summarize_with_gemini.ps1
    summarize_with_claude.ps1
    summarize_with_codex.ps1
    llm/
      common.ps1
      providers/
        gemini.ps1
        claude.ps1
        codex.ps1
```

### Componenti principali

- `scripts/watch_calls.ps1`: osserva `da_processare/` e lancia la pipeline quando arriva un file supportato.
- `scripts/process_call.ps1`: orchestratore principale. Stabilizza il file, prepara audio, trascrive, riassume, classifica, comprime, pulisce e rigenera gli indici.
- `scripts/transcribe_with_groq.ps1`: invia audio a Groq Whisper. Per file grandi crea chunk temporanei e ricompone la trascrizione.
- `scripts/prompt_riassunto_call.md`: prompt che guida il provider LLM a produrre un riassunto fedele e strutturato.
- `scripts/rebuild_indexes.ps1`: rigenera gli indici Markdown della knowledge base.
- `scripts/llm/common.ps1`: funzioni comuni ai provider LLM.
- `scripts/llm/providers/*.ps1`: moduli provider-specific.

## Requisiti

- Windows PowerShell.
- `ffmpeg` e `ffprobe` disponibili nel PATH.
- `curl.exe`.
- Variabile ambiente `GROQ_API_KEY`.
- Gemini CLI disponibile come comando `gemini`.
- `ripgrep` disponibile come comando `rg` per evitare fallback lenti della Gemini CLI.
- Claude CLI disponibile come comando `claude` solo se si usa `-p claude`.

### Installazione dipendenze

Esempio con `winget`:

```powershell
winget install Gyan.FFmpeg
winget install BurntSushi.ripgrep.MSVC
```

Configurare la chiave Groq:

```powershell
$env:GROQ_API_KEY = "..."
```

Per renderla persistente:

```powershell
[Environment]::SetEnvironmentVariable("GROQ_API_KEY", "...", "User")
```

Gemini CLI deve essere gia' autenticato. Verifica rapida:

```powershell
gemini --version
```

## Avvio rapido

1. Crea o verifica che esista `da_processare/`.
2. Crea le task sotto `completate/Task/`, ad esempio:

```text
completate/
  Task/
    Italgas - MCP Server/
    Integrazione Tabella Z_AUTH/
```

3. Avvia il watcher:

```bat
scripts\start_watcher.cmd
```

4. Copia una registrazione in `da_processare/`.
5. A fine processo trovi la call in:

```text
completate/Task/<task>/<data ora - titolo>/
```

## Uso manuale

Per processare una singola registrazione senza watcher:

```powershell
scripts\process_call.ps1 -InputPath .\da_processare\call.m4a
```

Per mantenere il video originale dopo la lavorazione:

```powershell
scripts\process_call.ps1 -InputPath .\da_processare\call.mp4 -KeepVideo
```

Per cambiare la soglia massima dell'audio archiviato:

```powershell
scripts\process_call.ps1 -InputPath .\da_processare\call.m4a -ArchiveMaxMB 25
```

## Provider LLM

Gemini e' il provider predefinito. Avviando la pipeline senza flag, `process_call.ps1` usa:

- `gemini-3.1-pro-preview` per il riassunto;
- `gemini-3-flash-preview` per la classificazione task.

Se Gemini restituisce un errore di capacita' sul modello, per esempio `MODEL_CAPACITY_EXHAUSTED` o `RESOURCE_EXHAUSTED`, la pipeline usa questa sequenza:

- 2 tentativi con `gemini-3.1-pro-preview`;
- 1 tentativo con `gemini-3-pro-preview`;
- fallback a Claude se anche Gemini 3 Pro non conclude.

Nel fallback Claude usa:

- `claude-sonnet-4-6` per il riassunto, con effort `medium`;
- subagent Claude `claude-haiku-4-5`, con effort `high`;
- classificazione task con il provider Claude gia' attivo dopo il fallback.

```powershell
scripts\process_call.ps1 -InputPath .\da_processare\call.m4a
```

Per selezionare un provider diverso usa `-p` o `-Provider`:

```powershell
scripts\process_call.ps1 -InputPath .\da_processare\call.m4a -p claude
```

Provider disponibili:

- `gemini`: default operativo.
- `claude`: provider alternativo mantenuto per compatibilita'.
- `codex`: placeholder futuro; oggi fallisce con un messaggio esplicito.

Puoi sovrascrivere i modelli:

```powershell
scripts\process_call.ps1 `
  -InputPath .\da_processare\call.m4a `
  -SummaryModel gemini-3.1-pro-preview `
  -TaskModel gemini-3-flash-preview
```

Puoi anche cambiare il numero di tentativi Gemini prima del fallback:

```powershell
scripts\process_call.ps1 `
  -InputPath .\da_processare\call.m4a `
  -GeminiCapacityAttempts 3
```

Il modello Gemini intermedio e' configurabile, ma di default resta `gemini-3-pro-preview`:

```powershell
scripts\process_call.ps1 `
  -InputPath .\da_processare\call.m4a `
  -GeminiFallbackModel gemini-3-pro-preview
```

Gli entrypoint standalone sono:

```powershell
scripts\summarize_with_gemini.ps1 -TranscriptPath .\trascrizione.txt
scripts\summarize_with_claude.ps1 -TranscriptPath .\trascrizione.txt
scripts\summarize_with_codex.ps1 -TranscriptPath .\trascrizione.txt
```

### Subagent

Il provider Gemini puo' usare subagent locali in `.gemini/agents/` se presenti nella macchina.

Il provider Claude definisce invece i subagent al volo via CLI, senza file tracciati nel repository:

- `call-metadata-auditor`: controllo di persone, sistemi, tag e frontmatter.
- `call-action-auditor`: controllo di decisioni, action item, dipendenze, domande aperte e citazioni.

## Output

Ogni call archiviata contiene:

```text
<titolo call>.md
audio_compresso.m4a
```

Nelle versioni recenti il file del riassunto viene rinominato usando il titolo della cartella, senza data e ora. Per esempio:

```text
2026-05-11 11.37 - Marco e Daniela, Autenticazione Claude su Databricks Italgas/
  Marco e Daniela, Autenticazione Claude su Databricks Italgas.md
```

Questo evita nodi duplicati chiamati tutti `riassunto` nel graph di Obsidian. Il nome legacy `riassunto.md` puo' ancora comparire come file temporaneo o in archivi vecchi, ma `rebuild_indexes.ps1` lo rinomina automaticamente quando rigenera gli indici.

Esempio di frontmatter:

```yaml
---
data: 2026-05-13
ora: "11:37"
task: "[[Italgas - MCP Server]]"
persone: [Daniela, Marco]
sistemi: [Databricks, Gemini CLI]
tags: [call, italgas, mcp-server, autenticazione]
---
```

Subito dopo il frontmatter il file usa sempre:

```markdown
# riassunto
## Titolo breve della call
```

## Knowledge base Obsidian

La cartella `completate/` puo' essere aperta direttamente come vault Obsidian.

La pipeline genera:

- `completate/README.md`: indice globale con task attive e ultime call.
- `completate/Task/<task>/README.md`: indice delle call della singola task.

I link sono wikilink Obsidian con path relativo, ad esempio:

```markdown
[[Task/Italgas - MCP Server/2026-05-13 11.37 - Autenticazione Databricks/riassunto|Autenticazione Databricks]]
```

Per rigenerare manualmente gli indici:

```powershell
scripts\rebuild_indexes.ps1
```

## Flusso end-to-end

1. Il file viene copiato in `da_processare/`.
2. Il watcher rileva il file.
3. `process_call.ps1` aspetta che dimensione e timestamp siano stabili.
4. `ffmpeg` estrae o converte l'audio in `audio.m4a`.
5. Groq Whisper produce `trascrizione.txt`.
6. Il provider LLM genera il Markdown del riassunto.
7. Lo script normalizza titolo e frontmatter.
8. Il provider LLM classifica la call rispetto alle task esistenti.
9. La cartella viene spostata sotto `completate/Task/<task>/`.
10. Il file del riassunto viene rinominato con il titolo della call.
11. L'audio viene compresso in `audio_compresso.m4a`.
12. I file intermedi vengono rimossi.
13. Gli indici Obsidian vengono rigenerati.

## Sviluppo

### Aggiungere un provider LLM

Per aggiungere un provider, crea un file in `scripts/llm/providers/` che esponga queste funzioni:

```powershell
Test-LlmProviderAvailable
Invoke-SummaryGeneration
Invoke-TaskClassification
Get-DefaultSummaryModel
Get-DefaultTaskModel
```

Poi aggiungi un entrypoint `scripts/summarize_with_<provider>.ps1` e registra il provider nel `ValidateSet` di `process_call.ps1`.

Il provider puo' usare strumenti diversi, ma deve restituire sempre lo stesso formato finale: Markdown puro validabile da `scripts/llm/common.ps1`.

### File esclusi dal repository

Registrazioni, audio compressi, trascrizioni, riassunti reali, log e vault generati sono esclusi da Git. Il repository deve contenere solo script, prompt, configurazioni e documentazione.

## Troubleshooting

### `GROQ_API_KEY` mancante

Imposta la variabile ambiente:

```powershell
$env:GROQ_API_KEY = "..."
```

### `ffmpeg` o `ffprobe` non trovati

Verifica:

```powershell
ffmpeg -version
ffprobe -version
```

### Gemini CLI non trovato

Verifica:

```powershell
gemini --version
```

### Ripgrep non trovato

Gemini CLI puo' stampare `Ripgrep is not available. Falling back to GrepTool.` quando `rg` non e' nel PATH del processo che ha avviato il watcher. Installa ripgrep e riavvia il terminale o il watcher:

```powershell
winget install BurntSushi.ripgrep.MSVC
rg --version
```

### La call finisce nella root di `completate/Task/`

Succede quando il provider LLM non riesce a classificare la call in una task esistente. Controlla che le cartelle task siano presenti e abbiano nomi descrittivi.

### Gli indici Obsidian non sono aggiornati

Esegui:

```powershell
scripts\rebuild_indexes.ps1
```

## Stato del progetto

Il progetto e' pensato per uso locale personale o di piccolo team. Non e' un servizio multiutente e non include database, embeddings o motori di ricerca esterni.
