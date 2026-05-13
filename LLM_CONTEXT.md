# Contesto per LLM

Questo progetto è una pipeline locale Windows per processare registrazioni di call di lavoro. L'obiettivo è prendere file audio o video inseriti in una cartella di input, trascriverli, riassumerli in Markdown, assegnare un titolo breve e archiviarli nella cartella task più coerente.

## Scopo

Il progetto automatizza il flusso:

1. ricezione di una registrazione;
2. estrazione o conversione dell'audio;
3. trascrizione con API di Whisper hostato su Groq;
4. sintesi ragionata della trascrizione con provider LLM modulare, Gemini di default;
5. generazione di un `riassunto.md` pulito;
6. completamento del frontmatter YAML per Obsidian;
7. compressione dell'audio per archivio;
8. classificazione della call in una cartella task;
9. pulizia dei file temporanei;
10. rigenerazione degli indici della knowledge base.

## Struttura cartelle

La root attesa è la cartella `Call`.

```text
Call/
  da_processare/
  completate/
    README.md
    Task/
      <nome task>/
        README.md
        <data ora - titolo call>/
          riassunto.md
          audio_compresso.m4a
  logs/
  scripts/
```

`da_processare/` contiene i file in ingresso.  
`completate/` puo' essere aperta come vault Obsidian. Contiene un README globale auto-generato.
`completate/Task/` contiene i task correnti. Ogni call processata viene spostata nella sottocartella task più adatta. Ogni task contiene un README auto-generato con i wikilink alle call.
`logs/` contiene log del watcher.  
`scripts/` contiene tutta la logica della pipeline.

## Componenti principali

`watch_calls.ps1` osserva `da_processare/` con un `FileSystemWatcher`. All'avvio processa anche eventuali file gia' presenti. Quando intercetta un file supportato, chiama `process_call.ps1`.

`process_call.ps1` è l'orchestratore. Riceve il path della registrazione, verifica che il file sia stabile, riconosce audio/video, prepara la cartella di lavoro, estrae o converte l'audio, lancia trascrizione e riassunto, normalizza il titolo preservando il frontmatter, classifica il task, completa `data`, `ora` e `task` nel frontmatter, comprime l'audio, rimuove i file intermedi e rigenera gli indici.

`transcribe_with_groq.ps1` invia l'audio alle API Groq usando `curl.exe` e il modello `whisper-large-v3-turbo`. Se il file supera la soglia configurata, lo divide in chunk con `ffmpeg`, trascrive ogni chunk e ricompone il testo.

`scripts/llm/common.ps1` contiene la logica comune ai provider LLM: composizione prompt, pulizia Markdown, validazione del riassunto, scrittura UTF-8 no-BOM, prompt di classificazione task e normalizzazione della risposta task.

`scripts/llm/providers/gemini.ps1`, `scripts/llm/providers/claude.ps1` e `scripts/llm/providers/codex.ps1` contengono solo la logica specifica del singolo provider. Ogni provider espone `Test-LlmProviderAvailable`, `Invoke-SummaryGeneration`, `Invoke-TaskClassification`, `Get-DefaultSummaryModel` e `Get-DefaultTaskModel`.

`summarize_with_gemini.ps1` è l'entrypoint di default per il riassunto e usa Gemini CLI con `gemini-3.1-pro-preview`. `summarize_with_claude.ps1` resta disponibile come entrypoint alternativo. `summarize_with_codex.ps1` esiste come placeholder futuro e fallisce con messaggio esplicito.

Il provider Gemini usa i subagent locali in `.gemini/agents/` come controllo interno per metadati e action item. `.gemini/settings.json` abilita esplicitamente questi agent nel progetto.

`prompt_riassunto_call.md` definisce il formato e le regole del riassunto. Il provider LLM deve restituire solo Markdown puro, senza provare a creare file, senza blocchi di codice e senza testo extra. Il prompt chiede una ricostruzione fedele della call, con dettagli specifici, action item in tabella, citazioni brevi quando utili e frontmatter con `persone`, `sistemi` e `tags`.

`rebuild_indexes.ps1` rigenera in modo idempotente gli indici Markdown: `completate/README.md` e `completate/Task/<task>/README.md`. Gli indici usano wikilink Obsidian con path relativo per evitare collisioni tra call con titoli simili.

`run_watcher_task.ps1` avvia il watcher in modalita' logging. `start_watcher.cmd` è un avvio manuale comodo da Windows.

## Flusso passo passo

1. L'utente copia un file `.mp4`, `.m4a`, `.mp3`, `.wav` o altro formato supportato in `da_processare/`, oppure aspetta che una registrazione video (ad esempio con OBS) termini.
2. Il watcher rileva il file e invoca `process_call.ps1`.
3. Lo script aspetta che dimensione e timestamp del file restino stabili per evitare di leggere un file ancora in copia (per il motivo indicato sopra, ovvero registrazione schermo).
4. Se il file è video, `ffmpeg` estrae l'audio mono a 16 kHz in `audio.m4a`. Se è audio, viene copiato o convertito nello stesso formato operativo.
5. La trascrizione viene prodotta con Groq. Per file grandi vengono creati chunk temporanei sotto `_chunks/`.
6. Gemini produce il riassunto seguendo il prompt, salvo override esplicito con `-p claude` o altro provider.
7. Il Markdown viene pulito e validato. Il file finale deve iniziare con frontmatter YAML e poi:

```md
---
data:
ora:
task:
persone: []
sistemi: []
tags: [call]
---

# riassunto
## Titolo breve della call
```

8. Il sottotitolo viene usato anche per rinominare la cartella della call nel formato `<yyyy-MM-dd HH.mm - titolo breve>`.
9. Lo script legge le sottocartelle in `completate/Task/` e chiede al provider LLM selezionato di scegliere il task più coerente rispetto a titolo e riassunto.
10. La cartella della call viene spostata dentro il task scelto.
11. Lo script completa il frontmatter con `data`, `ora` e `task: "[[Nome Task]]"` quando la call e' stata assegnata a una task.
12. L'audio viene compresso in `audio_compresso.m4a`, con target massimo configurabile.
13. Restano solo `riassunto.md` e `audio_compresso.m4a`; trascrizioni, chunk e audio intermedi vengono rimossi.
14. Gli indici della knowledge base vengono rigenerati.

## Convenzioni importanti

- Il nome del file Markdown finale è sempre `riassunto.md`.
- Il file Markdown finale contiene frontmatter YAML valido per Obsidian.
- Il titolo principale del Markdown è sempre `# riassunto`.
- Il sottotitolo è un titolo contestuale breve, massimo 5 o 6 parole.
- Il nome cartella usa lo stesso titolo contestuale del Markdown.
- `data`, `ora` e `task` vengono scritti dallo script, non dal provider LLM.
- `persone`, `sistemi` e `tags` vengono estratti dal provider LLM dalla trascrizione; `tags` deve includere `call` e usare kebab-case minuscolo.
- Gemini e' il provider di default. `process_call.ps1 -p claude` usa Claude; `-p codex` e' presente come placeholder ma non ancora operativo.
- I moduli provider devono restituire sempre lo stesso formato finale, indipendentemente dal tool usato.
- Gli indici auto-generati non vanno modificati a mano: eventuali cambi manuali vengono sovrascritti dal rebuild.
- Le call archiviate non devono essere versionate su Git, soltanto la logica del processo.
- Il repository deve contenere solo script, prompt e documentazione, non audio, trascrizioni, riassunti reali o log.

## Dipendenze esterne

- `ffmpeg` e `ffprobe` per conversione, chunking e compressione audio.
- `curl.exe` per chiamare le API Groq.
- variabile ambiente `GROQ_API_KEY` per autenticare Groq.
- Gemini CLI disponibile nel PATH come `gemini`.
- Claude CLI disponibile nel PATH come `claude` solo per il provider alternativo `-p claude`.

## Punti di attenzione

- Se il provider LLM restituisce testo operativo invece del riassunto, lo script deve fallire invece di salvare output non valido.
- Se il provider LLM non riesce a classificare il task, la call finisce nella root `completate/Task/` come fallback.
- Se esiste gia' una cartella con lo stesso nome, viene creato un suffisso progressivo tipo `(2)`.
- `rebuild_indexes.ps1` e' stateless: rilegge sempre il filesystem e riscrive completamente gli indici.
- La pipeline è progettata per uso locale personale, non come servizio multiutente.
