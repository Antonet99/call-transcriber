# Contesto per LLM

Questo progetto e' una pipeline locale Windows per processare registrazioni di call di lavoro. L'obiettivo e' prendere file audio o video inseriti in una cartella di input, trascriverli, riassumerli in Markdown, assegnare un titolo breve e archiviarli nella cartella task piu' coerente.

## Scopo

Il progetto automatizza il flusso:

1. ricezione di una registrazione;
2. estrazione o conversione dell'audio;
3. trascrizione con Groq Whisper;
4. sintesi ragionata con Claude CLI;
5. generazione di un `riassunto.md` pulito;
6. compressione dell'audio per archivio;
7. classificazione della call in una cartella task;
8. pulizia dei file temporanei.

## Struttura cartelle

La root attesa e' la cartella `Call`.

```text
Call/
  da_processare/
  completate/
    Task/
      <nome task>/
        <data ora - titolo call>/
          riassunto.md
          audio_compresso.m4a
  logs/
  scripts/
```

`da_processare/` contiene i file in ingresso.  
`completate/Task/` contiene i task correnti. Ogni call processata viene spostata nella sottocartella task piu' adatta.  
`logs/` contiene log del watcher.  
`scripts/` contiene tutta la logica della pipeline.

## Componenti principali

`watch_calls.ps1` osserva `da_processare/` con un `FileSystemWatcher`. All'avvio processa anche eventuali file gia' presenti. Quando intercetta un file supportato, chiama `process_call.ps1`.

`process_call.ps1` e' l'orchestratore. Riceve il path della registrazione, verifica che il file sia stabile, riconosce audio/video, prepara la cartella di lavoro, estrae o converte l'audio, lancia trascrizione e riassunto, normalizza il titolo, classifica il task, comprime l'audio e rimuove i file intermedi.

`transcribe_with_groq.ps1` invia l'audio alle API Groq usando `curl.exe` e il modello `whisper-large-v3-turbo`. Se il file supera la soglia configurata, lo divide in chunk con `ffmpeg`, trascrive ogni chunk e ricompone il testo.

`summarize_with_claude.ps1` legge il prompt in `prompt_riassunto_call.md`, concatena la trascrizione e invoca `claude -p`. L'output viene ripulito: se Claude restituisce un blocco fenced Markdown, viene estratto solo il contenuto; frasi introduttive non desiderate vengono scartate. Lo script valida che il risultato abbia `# riassunto`, un sottotitolo `## ...` e sezioni `### ...`.

`prompt_riassunto_call.md` definisce il formato e le regole del riassunto. Claude deve restituire solo Markdown puro, senza provare a creare file, senza blocchi di codice e senza testo extra.

`run_watcher_task.ps1` avvia il watcher in modalita' logging. `start_watcher.cmd` e' un avvio manuale comodo da Windows.

## Flusso passo passo

1. L'utente copia un file `.mp4`, `.m4a`, `.mp3`, `.wav` o altro formato supportato in `da_processare/`.
2. Il watcher rileva il file e invoca `process_call.ps1`.
3. Lo script aspetta che dimensione e timestamp del file restino stabili per evitare di leggere un file ancora in copia.
4. Se il file e' video, `ffmpeg` estrae l'audio mono a 16 kHz in `audio.m4a`. Se e' audio, viene copiato o convertito nello stesso formato operativo.
5. La trascrizione viene prodotta con Groq. Per file grandi vengono creati chunk temporanei sotto `_chunks/`.
6. Claude produce il riassunto seguendo il prompt.
7. Il Markdown viene pulito e validato. Il file finale deve iniziare con:

```md
# riassunto
## Titolo breve della call
```

8. Il sottotitolo viene usato anche per rinominare la cartella della call nel formato `<yyyy-MM-dd HH.mm - titolo breve>`.
9. Lo script legge le sottocartelle in `completate/Task/` e chiede a Claude di scegliere il task piu' coerente rispetto a titolo e riassunto.
10. La cartella della call viene spostata dentro il task scelto.
11. L'audio viene compresso in `audio_compresso.m4a`, con target massimo configurabile.
12. Restano solo `riassunto.md` e `audio_compresso.m4a`; trascrizioni, chunk e audio intermedi vengono rimossi.

## Convenzioni importanti

- Il nome del file Markdown finale e' sempre `riassunto.md`.
- Il titolo principale del Markdown e' sempre `# riassunto`.
- Il sottotitolo e' un titolo contestuale breve, massimo 5 o 6 parole.
- Il nome cartella usa lo stesso titolo contestuale del Markdown.
- Le call archiviate non devono essere versionate su Git.
- Il repository deve contenere solo script, prompt e documentazione, non audio, trascrizioni, riassunti reali o log.

## Dipendenze esterne

- `ffmpeg` e `ffprobe` per conversione, chunking e compressione audio.
- `curl.exe` per chiamare le API Groq.
- variabile ambiente `GROQ_API_KEY` per autenticare Groq.
- Claude CLI disponibile nel PATH come `claude`.

## Punti di attenzione

- Se Claude restituisce testo operativo invece del riassunto, `summarize_with_claude.ps1` deve fallire invece di salvare output non valido.
- Se Claude non riesce a classificare il task, la call finisce nella root `completate/Task/` come fallback.
- Se esiste gia' una cartella con lo stesso nome, viene creato un suffisso progressivo tipo `(2)`.
- La pipeline e' progettata per uso locale personale, non come servizio multiutente.
