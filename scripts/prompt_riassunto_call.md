# Istruzioni Riassunto Call

## Obiettivo

- Leggere tutta la trascrizione prima di sintetizzare.
- Ricostruire il contenuto in forma più chiara e scorrevole senza perdere informazioni essenziali.
- Conservare dettagli tecnici, nomi propri, decisioni, task, dubbi aperti, vincoli, rischi e follow-up.

## Procedura

1. Identificare il contesto della call e i temi principali emersi.
2. Separare ciò che è stato deciso da ciò che è stato solo proposto, ipotizzato o lasciato aperto.
3. Estrarre task, owner espliciti, dipendenze, blocchi e prossimi passi quando compaiono nella trascrizione.
4. Raggruppare i contenuti per argomento invece di seguire alla lettera l'ordine frammentato della conversazione, salvo quando la sequenza temporale è importante.
5. Segnalare esplicitamente i passaggi ambigui, incompleti o poco comprensibili invece di interpretarli in modo arbitrario.

## Regole

- Restituire solo testo Markdown puro come risposta.
- Non provare a creare, scrivere, modificare o salvare file: il salvataggio viene fatto automaticamente da uno script esterno.
- Non scrivere frasi introduttive, ad esempio "Leggo la trascrizione e produco il riassunto."
- Non racchiudere il risultato in blocchi di codice come ```md o ```.
- Non inventare dettagli non presenti nella trascrizione.
- Non omettere decisioni, task, dubbi, vincoli o rischi solo perché espressi in modo confuso.
- Mantenere termini tecnici, nomi di modelli, sistemi, feature, issue, ticket, repository e dipendenze.
- Rendere il testo più leggibile, ma senza cambiare il significato di quanto detto.
- Indicare chiaramente quando un'informazione è incerta o derivata da un passaggio poco chiaro.
- Generare un titolo di contesto brevissimo, massimo 5 o 6 parole, specifico e comprensibile.

## Struttura Consigliata Dell Output

Il file deve iniziare esattamente con:

# riassunto
## Titolo brevissimo del contesto

Sostituire "Titolo brevissimo del contesto" con un titolo concreto della call, massimo 5 o 6 parole.
Non usare "Riassunto Call AI" come titolo o sottotitolo.
Non ripetere `# riassunto` o il sottotitolo in altre parti del file.

Usare poi sezioni brevi e facili da scorrere, come titoli di terzo livello. Includere solo quelle supportate dal contenuto disponibile:

- Contesto
- Decisioni prese
- Punti discussi
- Task e action item
- Blocchi, dubbi o rischi
- Prossimi passi
- Passaggi ambigui da verificare

## Criteri Di Qualita

- Produrre un riassunto più ordinato della trascrizione originale.
- Mantenere completezza e precisione anche quando il testo sorgente è rumoroso.
- Evidenziare chiaramente ciò che richiede verifica o follow-up.
