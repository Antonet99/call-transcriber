# Prestazioni del watcher

Misure del 15 ottobre 2026: base `b561cb6` e modifiche su `feat/watcher-performance`.
Il benchmark usa Python 3.11.9 del venv, Windows, Rich 15.0.0 e watchdog 6.0.0.

## Cambiamenti

1. Gli eventi duplicati di un file gia' in coda non ripetono la risoluzione del percorso. Il thread di disponibilita' continua a verificare dimensione, timestamp e accesso al file. Una seconda verifica sotto lock impedisce inserimenti duplicati durante eventi concorrenti.
2. La scansione mantiene l'ordine cronologico, ignora subito le estensioni non supportate e non riesamina dal journal gli stessi percorsi appena incontrati. I percorsi mancanti recuperabili continuano a essere cercati nel journal.
3. Il catalogo verifica il confinamento di ogni cartella quando la estrae dalla pila, prima di leggerla; non ripete lo stesso controllo anche nell'inserimento in pila. Per i file, filtra prima le estensioni irrilevanti. Restano i controlli su cartella selezionata, originale, link e percorsi esterni.
4. Prima di modificare il journal si copia soltanto la voce interessata per poterla ripristinare in caso di errore. Scrittura atomica, backup, formato del file e persistenza immediata restano invariati. Un errore su una nuova voce la rimuove dalla memoria; un errore su una voce esistente ne ripristina il contenuto precedente.

## Confronto prima/dopo

Cinque esecuzioni per scenario e versione, sugli stessi file sintetici e sugli stessi percorsi, alternando l'ordine delle versioni. Nessuna suite test concorrente.

| Operazione | Prima: mediana (min–max) | Dopo: mediana (min–max) | Riduzione mediana |
|---|---:|---:|---:|
| Scansione iniziale, 10 file | 264 ms (249–489) | 336 ms (218–411) | Nessun vantaggio dimostrato |
| Scansione iniziale, 100 file | 3,73 s (3,11–5,65) | 2,67 s (2,47–2,81) | 29% |
| Scansione iniziale, 300 file | 11,76 s (9,22–13,91) | 8,43 s (6,11–8,57) | 28% |
| Scansione ripetuta, 10 file | 154 ms (102–264) | 10,3 ms (3,0–15,7) | 93% |
| Scansione ripetuta, 100 file | 1,30 s (1,09–2,18) | 62,8 ms (53,5–75,1) | 95% |
| Scansione ripetuta, 300 file | 3,72 s (2,59–4,33) | 123 ms (102–209) | 97% |
| Catalogo, 20 salvate | 960 ms (894–963) | 546 ms (509–655) | 43% |
| Catalogo, 200 salvate | 9,38 s (9,18–11,98) | 5,71 s (5,50–6,64) | 39% |

La scansione ripetuta e' quella eseguita periodicamente dal menu, anche se i file sono invariati. Ridurne il costo accorcia il blocco sincrono prima della lettura del prossimo tasto. I tempi di avvio completo del launcher e la latenza dei tasti nella console fisica non sono stati misurati con questo confronto.

Per distinguere i due interventi sul watcher e' stata misurata anche una versione intermedia con le sole ottimizzazioni dei percorsi. Con 300 file, la scansione iniziale passa da 11,76 s a 9,91 s con il primo intervento e a 8,43 s con la copia della sola voce del journal. La mediana CPU passa rispettivamente da 8,69 s a 6,61 s e 5,08 s. Con 100 file il secondo intervento non mostra un vantaggio distinguibile dalla variabilita': 2,65 s contro 2,67 s.

Il file `results-2026-10-15.json` conserva i campioni: `base` identifica b561cb6, `paths` la versione intermedia e `final` quella consegnata. Contiene anche un test di stress separato del journal con 1000 voci e configurazioni annidate sintetiche; non rappresenta lo storico reale dell'utente e non viene usato per stimare il tempo di avvio.

## Limiti e costi residui

- I file sono sintetici, fuori dal vault e da OneDrive. La cache del sistema operativo non viene svuotata. Il risultato non quantifica sincronizzazione OneDrive o avvio a disco freddo.
- Il catalogo contiene un originale, un marker JSON e una scheda Markdown per call. Il benchmark verifica anche che prima/dopo restituiscano gli stessi elementi. Lo storico reale, il cestino e i checkpoint restano coperti dai test funzionali; non da questi tempi.
- Su piccole code non e' dimostrato un miglioramento della scansione iniziale: la variabilita' e il costo delle scritture prevalgono.
- Le tre verifiche di stabilita', l'intervallo predefinito di un secondo e il controllo di accesso Windows non sono stati ridotti. Il benchmark isola la scansione dal thread di disponibilita', usando un intervallo di 3600 secondi solo nei suoi handler sintetici.
- Il tempo CPU comprende il lavoro del processo; la differenza rispetto al tempo trascorso non e' una misura pura dell'I/O. Su Windows la risoluzione del timer CPU e' limitata per operazioni brevi.
- Journal e backup vengono ancora scritti per ogni aggiornamento: resta un costo crescente con lo storico, ma senza introdurre finestre di perdita di stato.
- La manutenzione resta sincrona prima del menu e periodicamente. La prima analisi ha misurato circa 2 s con 200 call correnti, senza archiviazioni effettive. Non viene presentata come ottimizzata.
- Nessuna modifica al rendering, ai provider, a FFmpeg, agli hash o alle regole di conservazione. Nessuna riduzione della latenza Groq/Claude e' deducibile da queste prove.

## Ripetere le misure

Dalla root del repository:

```powershell
.\.venv\Scripts\python.exe -X utf8 -B benchmarks\watcher_performance.py
```

Per una verifica breve:

```powershell
.\.venv\Scripts\python.exe -X utf8 -B benchmarks\watcher_performance.py --runs 1 --sizes 10 --catalog-sizes 20
```

I risultati vengono stampati in JSON. `--baseline-ref` seleziona la revisione Git di confronto; il valore predefinito e' `b561cb6`. `--temp-dir` sceglie la cartella dei dati sintetici. Non usare la cartella delle registrazioni reali. I moduli di base sono caricati da Git e condividono le altre dipendenze della checkout: questo script e' adatto a queste modifiche circoscritte, non a confronti arbitrari fra architetture diverse.

## Verifica funzionale

150 test passati tramite `tests/run_isolated.py`. Cinque nuovi test verificano crescita del file e tre controlli di stabilita' dopo eventi duplicati, ordine cronologico e rifiuto delle sorgenti esterne, rifiuto di cartelle e file che risolvono fuori dal vault, rollback del journal con piu' voci e riapertura dello stato persistito dopo un errore.

Nessun watcher operativo, registrazione reale o provider e' stato avviato per queste verifiche.
