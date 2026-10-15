# Call Transcriber

Pipeline locale Windows per trasformare registrazioni audio/video di call in una knowledge base Markdown pronta per Obsidian.

Il progetto prende file audio o video, estrae l'audio, lo trascrive con Groq Whisper, genera un riassunto Markdown fedele alla conversazione con Claude CLI, archivia l'audio compresso e aggiorna indici navigabili con wikilink.

## Istruzioni operative per l'agente

Questo README è anche il contesto operativo della pipeline. Prima di generare o revisionare un riassunto, l'agente deve leggerlo insieme a `scripts/prompt_riassunto_call.md`, all'indice globale `VAULT_ROOT/completate/README.md` e, dopo il riconoscimento preliminare della task, al `README.md` della task assegnata. Le istruzioni e il contesto vanno inclusi nel prompt inviato a Claude; la trascrizione resta la fonte primaria per i fatti della call.

L'agente deve distinguere fatti confermati, ipotesi e informazioni mancanti, non inventare persone, decisioni, owner o scadenze, considerare più autorevoli le call più recenti in caso di conflitto e non leggere né riportare `.env`, token o chiavi API. La classificazione può proporre una task preliminare, ma deve essere verificata dopo la generazione del riassunto.

## Funzionalita'

- Watch della cartella `vault/da_processare/` con menu interattivo e coda persistente.
- Scelta esplicita per ogni registrazione: trascrivi e riassumi, salva solo l'originale, oppure scarta nel cestino per 30 giorni.
- Supporto a file audio e video comuni (`.m4a`, `.mp3`, `.wav`, `.mp4`, `.mkv`, `.mov`, ecc.).
- Trascrizione con Groq Whisper (`whisper-large-v3-turbo`).
- Riuso automatico di `trascrizione.txt` se una lavorazione precedente e' fallita dopo Whisper.
- Riassunti Markdown dettagliati, non generici, con:
  - frontmatter YAML per Obsidian;
  - titolo breve con nomi dei partecipanti;
  - sezioni granulari;
  - action item in tabella;
  - decisioni, dubbi, dipendenze e citazioni rilevanti.
- Claude CLI usa `claude-sonnet-5` per riassunti, classificazione e Kanban.
- Classificazione automatica della call dentro una cartella task oppure in `completate/Senza progetto` per call generiche/non collegate a un progetto.
- Archiviazione automatica delle call piu' vecchie di 15 giorni.
- Conservazione del video nella cartella della call per 15 giorni; il video viene eliminato quando la cartella viene archiviata.
- Aggiornamento automatico della Kanban di progetto con card estratte dal riassunto.
- Compressione dell'audio archiviato sotto una soglia configurabile.
- Indici Obsidian auto-generati:
  - indice globale;
  - indice per task con sezione archivio;
  - sezione globale delle call senza progetto.

## Architettura

```text
call-transcriber/          ← codice Python, launcher e configurazione
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
        claude.py
  .env                     ← chiavi API (non tracciato)
  pyproject.toml

Call/
  vault/                   ← vault Obsidian vero
    da_processare/         ← OBS deve salvare qui le registrazioni
    completate/
      README.md            ← indice globale auto-generato
      archivio/            ← eventuali sorgenti audio legacy non associati
      Task/
        <nome task>/
          README.md        ← indice task auto-generato
          Kanban.md        ← kanban auto-aggiornata
          <YYYY-MM-DD HH.mm - titolo>/
            <titolo call>.md
            <titolo call>.mp4 ← video conservato fino all'archiviazione
            audio_compresso.m4a
          archivio/        ← call piu' vecchie di ARCHIVE_DAYS
      Senza progetto/     ← call senza task riconosciuta (nessun README di progetto)
    logs/
```

## Requisiti di sistema

- Python 3.11+
- `ffmpeg` e `ffprobe` nel PATH
- Claude Code CLI installata e autenticata

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
VAULT_ROOT=C:\Users\ABAIO\OneDrive - ICONSULTING S.p.A\Desktop\Call\vault
GROQ_API_KEY=<la-tua-chiave-groq>
```

La generazione e la classificazione usano esclusivamente Claude CLI, autenticata tramite `claude auth login`.

## Avvio automatico al login

Registra il watcher come task di Windows (una tantum):

```powershell
.\scripts\register_startup_task.ps1
```

Da questo momento il launcher si avvia ad ogni login nella sessione utente interattiva. Lo script rimuove anche il vecchio task PowerShell `Call Automation Watcher`, se presente, per evitare watcher duplicati. Il watcher recupera anche i file gia' presenti in `VAULT_ROOT\da_processare\` all'avvio e attende sempre una scelta. Se stdin o output non sono un terminale interattivo, conserva la coda ed esce senza avviare elaborazioni o manutenzione: aprire il launcher in una console. Il rilevamento riprende al successivo avvio.

Comandi utili:

```powershell
Start-ScheduledTask -TaskName 'CallWatcher'   # avvia subito
Stop-ScheduledTask  -TaskName 'CallWatcher'   # ferma
Unregister-ScheduledTask -TaskName 'CallWatcher' -Confirm:$false  # rimuovi
```

## Avvio manuale del watcher

```powershell
.\scripts\start_watcher.cmd
```

### Menu e coda delle registrazioni

Il terminale usa una schermata che si aggiorna sul posto mantenendo lo stile ciano. Le righe selezionabili sono evidenziate; gli elenchi lunghi scorrono con la selezione. L'esito dell'ultima operazione resta visibile in forma compatta.

| Tasto | Azione |
|---|---|
| `↑` / `↓` | Sposta la selezione. |
| `Invio` | Conferma l'elemento selezionato o il testo visibile. |
| `Esc` | Torna alla schermata precedente. |
| `Q` | Apre la coda dei nuovi arrivi dalla schermata principale. |
| `N` | Apre le registrazioni non elaborate dalla schermata principale. |
| `Ctrl+C` | Ferma il watcher conservando coda e decisioni. |

Per una nuova call selezionare `Trascrivi e riassumi`, `Salva solo registrazione` oppure `Scarta`, poi premere Invio. Lo scarto sposta subito il file nel cestino per 30 giorni, senza ulteriori conferme. Nei menu non occorre digitare numeri; l'input libero serve soltanto per modificare il titolo, proposto già compilato. Invio non torna indietro: negli elenchi vuoti non esegue alcuna azione, mentre Esc chiude la vista.

La coda procede per ordine di disponibilità; i file preesistenti sono rilevati in ordine cronologico. Gli eventi duplicati dello stesso file non producono altre voci. Un file in scrittura non blocca gli altri: il watcher verifica dimensione, timestamp e, su Windows, disponibilità del file rispetto agli accessi in scrittura. La vista della coda aggiorna le voci quando diventano disponibili.

Mentre una pipeline è in corso restano visibili i progressi attuali. Le nuove registrazioni continuano a essere rilevate; il menu ritorna alla conclusione. I tasti premuti durante l'elaborazione vengono scartati. Il comando diretto `process_call.py` resta invece un avvio esplicito della pipeline completa.

Stato, ordine e scelte sono conservati in `.pipeline/watcher-state.json`, con copia `.json.bak`. Al riavvio i file presenti vengono ricontrollati; per le operazioni interrotte selezionare `Riprendi l'operazione già scelta` e confermare con Invio. Nessuna ripartenza automatica delle chiamate ai provider. Una decisione viene salvata prima di iniziare l'operazione. Gli errori tornano nel menu con stato recuperabile e dettagli nel log. Se il journal principale è illeggibile viene usata la copia di sicurezza; se entrambe le copie sono illeggibili il watcher si ferma senza sovrascriverle o muovere registrazioni.

### Registrazioni non elaborate

Premendo `N` si apre direttamente un unico elenco, diviso nelle sezioni `Salvate nelle cartelle` e `Nel cestino`. Selezionare una registrazione con le frecce e premere Invio per aprire i dettagli, poi confermare l'azione `Trascrivi e riassumi` o `Riprendi elaborazione`. Esc torna all'elenco e poi alla schermata principale. Aprire le viste non avvia provider, trascrizioni o elaborazioni.

Compaiono soltanto registrazioni riconosciute dai metadati del programma, con l'originale disponibile e senza una pipeline completa già conclusa. Sono incluse le cartelle archiviate quando l'originale esiste ancora. Una lavorazione interrotta resta disponibile per la ripresa dai checkpoint anche quando il file è già stato trasferito nella destinazione finale. I file mancanti senza checkpoint recuperabile e gli elementi già elaborati vengono esclusi.

Per le registrazioni salvate vengono mantenuti cartella, titolo e progetto scelti manualmente. Gli output sono preparati nell'area temporanea della pipeline; la scheda viene sostituita dal riassunto e vengono aggiunti trascrizione e audio compresso. L'originale resta nella cartella. Per il cestino la pipeline ricava titolo e progetto come nel flusso completo, trasferendo la registrazione nella cartella della call. Un elemento scompare dall'elenco solo al completamento dell'intero processo.

Le scadenze restano quelle esistenti: video salvati eliminati all'archiviazione dopo 15 giorni, cestino ripulito dopo 30 giorni. Se l'originale è già stato eliminato non è possibile trascriverlo dalla sola scheda. Le registrazioni con elaborazione avviata e interrotta vengono protette dalla pulizia finché serve riprenderle.

### Salvataggio della sola registrazione

Il titolo è proposto già compilato con il nome originale del file. Il progetto viene scelto con le frecce da un elenco di progetti attivi, che include `Senza progetto`. Invio conferma; Esc torna alla modifica del titolo e da lì al menu. La struttura resta quella delle altre call:

```text
completate/Task/<progetto>/YYYY-MM-DD HH.mm - Titolo/
  Titolo.mp4                 # registrazione originale, invariata nei contenuti
  Titolo.md                  # scheda locale: data, titolo, progetto e link
  .recording-job.json         # identificazione per ripresa dopo interruzione
```

Per `Senza progetto` si usa `completate/Senza progetto/`. Non vengono estratti o compressi audio, generate trascrizioni o riassunti, invocati provider o aggiornate Kanban. La scheda è inclusa negli indici Obsidian. I nomi vengono normalizzati per Windows e le collisioni ricevono un suffisso, senza sovrascritture.

Si applica la stessa archiviazione delle altre call: dopo `ARCHIVE_DAYS` (15 giorni) la cartella passa in `archivio` e il video viene eliminato. Poiché questa modalità non genera audio o trascrizione, dopo l'eliminazione del video resta soltanto la scheda. Gli originali audio seguono la normale regola di conservazione degli audio nelle cartelle call.

### Cestino delle registrazioni

La conferma di `Scarta` autorizza lo spostamento immediato nel cestino e la cancellazione automatica dopo 30 giorni dalla data di scarto, registrata nei metadati. La manutenzione verifica i metadati e l'identità del file prima di eliminarlo. File estranei, collegamenti simbolici o metadati corrotti non vengono cancellati. La pulizia avviene all'avvio interattivo e periodicamente mentre il watcher è attivo; se è spento viene differita al successivo avvio.

Per recuperare una registrazione prima della scadenza, fermare il watcher e spostare l'originale dalla sua sottocartella del cestino a `da_processare` con un nome libero. Il percorso originale è riportato in `metadata.json`. Al riavvio verrà rilevata come una registrazione da scegliere.

## Uso manuale (singola call)

```powershell
.\.venv\Scripts\python.exe scripts\process_call.py --input-path ..\Call\vault\da_processare\call.m4a
```

Per i video non serve piu' un'opzione dedicata: il video viene sempre spostato nella cartella finale della call e rinominato con il titolo del riassunto. L'argomento `--keep-video` resta accettato per compatibilita' con vecchi comandi, ma non cambia questo comportamento.

```powershell
.\.venv\Scripts\python.exe scripts\process_call.py --input-path ..\Call\vault\da_processare\call.mp4
```

## Configurazione

Tutti i parametri sono in `scripts/settings.py`:

```python
# Claude CLI
CLAUDE_SUMMARY_MODEL = "claude-sonnet-5"
CLAUDE_SUMMARY_EFFORT = "medium"
CLAUDE_TASK_MODEL = "claude-sonnet-5"
CLAUDE_LIGHT_MODEL = "claude-sonnet-5"
CLAUDE_SUMMARY_RETRIES = 2

# Groq / Trascrizione
GROQ_WHISPER_MODEL        = "whisper-large-v3-turbo"
TRANSCRIPTION_MAX_MB      = 19.0
TRANSCRIPTION_CHUNK_TARGET_MB = 18.0

# Pipeline
ARCHIVE_MAX_MB  = 19.0
ARCHIVE_DAYS    = 15
SOURCE_ARCHIVE_DAYS = 15
UNASSIGNED_CALLS_DIR_NAME = "Senza progetto"

# Kanban
KANBAN_MAX_CARDS_PER_CALL = 4
```

`SOURCE_ARCHIVE_DAYS` resta per la pulizia dei sorgenti audio legacy nell'archivio generale. I video legacy non associati vengono lasciati per la verifica manuale; i video nuovi seguono invece il ciclo della cartella call e vengono eliminati al momento dell'archiviazione dopo 15 giorni.

## Claude CLI

Claude CLI è l'unico motore LLM della pipeline.

Il flusso qualitativo del riassunto resta composto da piu' passaggi:

1. riconoscimento preliminare della task usando trascrizione, indice globale e task attive; il classificatore può restituire `NESSUNA_TASK` per una call senza progetto;
2. assemblaggio del prompt con `scripts/prompt_riassunto_call.md`, questo README, l'indice globale e il README della task preliminare;
3. draft del riassunto con Claude;
4. audit interni tramite i subagent Claude e revisione finale;
5. validazione locale del formato e retry se il Markdown non e' valido;
6. classificazione finale dopo il riassunto; `NESSUNA_TASK`, un output non riconoscibile o un errore tecnico instradano la call in `completate/Senza progetto`.

## Output

Ogni call elaborata produce:

```text
completate/Task/<task>/<YYYY-MM-DD HH.mm - Titolo>/
  <Titolo>.md            ← riassunto Markdown
  <Titolo>.mp4           ← video originale, conservato fino all'archiviazione
  trascrizione.txt        ← trascrizione completa riusabile in caso di retry
  audio_compresso.m4a    ← audio compresso sotto ARCHIVE_MAX_MB
```

Per una call senza progetto, la stessa struttura viene usata sotto `completate/Senza progetto/`, senza README o Kanban di progetto.

Esempio di frontmatter generato:

```yaml
---
data: 2026-05-13
ora: "11:37"
task: "[[Italgas - MCP Server]]"
persone: [Daniela, Marco]
sistemi: [Databricks, Claude Code]
tags: [call, italgas, mcp-server]
---
```

## Flusso end-to-end

1. Il file viene copiato in `VAULT_ROOT\da_processare\`.
2. Il watcher rileva il file.
3. Attesa finche' il file è disponibile e dimensione e timestamp sono stabili; la registrazione entra nella coda persistente del menu.
4. Dopo la conferma di `Trascrivi e riassumi`, `ffmpeg` estrae o converte l'audio in `audio.m4a`. Le azioni `Salva solo registrazione` e `Scarta` seguono i flussi locali descritti sopra.
5. Se `trascrizione.txt` esiste gia' nella cartella della call, viene riusata; altrimenti Groq Whisper la produce con chunking automatico per file grandi.
6. La pipeline riconosce preliminarmente la task usando `completate/README.md` e i README disponibili.
7. Claude genera il riassunto usando prompt, README root, indice globale e README task preliminare.
8. Titolo e frontmatter vengono normalizzati.
9. La classificazione finale verifica la task preliminare usando riassunto, trascrizione, indice globale e README di tutte le task.
10. La cartella viene spostata sotto `VAULT_ROOT\completate\Task\<task>\` oppure `VAULT_ROOT\completate\Senza progetto\` se non c'è una task assegnabile.
11. L'audio viene compresso in `audio_compresso.m4a`.
12. I file intermedi vengono rimossi, mantenendo riassunto, trascrizione, audio compresso e, per i video, il video rinominato con il titolo del riassunto.
13. Dopo 15 giorni la cartella della call viene spostata in `archivio` e il solo video viene eliminato; riassunto, trascrizione e audio restano disponibili.
14. Gli eventuali video gia' presenti nell'archivio generale vengono migrati nella call corrispondente quando la corrispondenza e' univoca.
15. Gli indici Obsidian vengono rigenerati; i README dei task attivi aggiornano solo il blocco call generato.
16. Se la call appartiene a una task, la Kanban del progetto viene aggiornata con le nuove card; per `Senza progetto` questo passaggio non si applica.

## Knowledge base Obsidian

La cartella `C:\Users\ABAIO\OneDrive - ICONSULTING S.p.A\Desktop\Call\vault` e' il vault Obsidian da aprire.

La pipeline genera automaticamente:

- `completate/README.md`: indice globale con task attive, task archiviate, call senza progetto e ultime N call.
- `completate/Task/<task>/README.md`: indice della singola task con call attive e archivio.

Le call senza progetto sono elencate nella sezione `Call senza progetto` dell'indice globale e non partecipano alla scoperta o allo scoring delle task.

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
  --summary-path "..\Call\vault\completate\Task\<task>\<call>\<titolo>.md" `
  --task-directory "..\Call\vault\completate\Task\<task>"
```

### File esclusi dal repository

Registrazioni, audio compressi, trascrizioni, riassunti, log, vault generati e `.env` sono esclusi da Git.

## Troubleshooting

**`GROQ_API_KEY` mancante**: aggiungi la chiave al file `.env`.

**`ffmpeg` non trovato**: `winget install Gyan.FFmpeg` e riavvia il terminale.

**Claude CLI non disponibile**: verifica che `claude` sia installato, presente nel PATH e autenticato.

**La call finisce in `completate/Senza progetto`**: il classificatore ha scelto `NESSUNA_TASK`, ha restituito un output non riconoscibile oppure ha avuto un errore tecnico. Verifica il log per distinguere la scelta semantica dal fallback.

**Gli indici non sono aggiornati**: esegui `rebuild_indexes.py` manualmente.
