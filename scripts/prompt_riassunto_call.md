# Istruzioni Riassunto Call

## Obiettivo

Non limitarti a "riassumere": ricostruisci fedelmente quanto detto nella call, in forma chiara, ordinata e navigabile.

Il risultato deve permettere a chi non ha ascoltato la call di capire:

- cosa e' stato detto davvero;
- quali nomi, sistemi, numeri, date, repository, ticket, issue o vincoli sono emersi;
- quali decisioni sono state prese;
- quali action item, dipendenze, blocchi o dubbi restano aperti;
- quali frasi sono importanti da rileggere quasi testualmente.

## Regole generali

- Restituire solo Markdown puro come risposta.
- Non creare, scrivere, modificare o salvare file: il salvataggio viene fatto automaticamente da uno script esterno.
- Non scrivere frasi introduttive.
- Non racchiudere il risultato in blocchi di codice come ```md o ```.
- Non inventare dettagli non presenti nella trascrizione.
- Non trasformare dettagli specifici in frasi generiche.
- Conservare nomi propri, sistemi, numeri, date, ticket, issue, repository, acronimi e terminologia tecnica esattamente come compaiono nella trascrizione.
- Se un passaggio e' ambiguo, incompleto o poco comprensibile, segnalalo come tale invece di interpretarlo.
- Generare un titolo di contesto brevissimo, massimo 5 o 6 parole, specifico e comprensibile.
- Usare sezioni `###` granulari per argomento, non una scaletta fissa sempre uguale.

## Frontmatter YAML

Il file deve iniziare con un frontmatter YAML. Compila solo i campi deducibili dalla trascrizione.

- `data`, `ora` e `task` possono essere lasciati vuoti o omessi: verranno completati dallo script.
- `persone`: nomi delle persone citate o partecipanti, se emergono.
- `sistemi`: sistemi, tool, piattaforme, repository, servizi o prodotti citati.
- `tags`: tag in kebab-case minuscolo. Includere sempre `call`.

Esempio:

---
data:
ora:
task:
persone: [Daniela, Marco]
sistemi: [Databricks, Claude Code]
tags: [call, italgas, mcp-server, autenticazione]
---

## Struttura obbligatoria

Dopo il frontmatter il file deve continuare esattamente con:

# riassunto
## Titolo brevissimo del contesto

Sostituire "Titolo brevissimo del contesto" con un titolo concreto della call, massimo 5 o 6 parole.
Non usare "Riassunto Call AI" come titolo o sottotitolo.
Non ripetere `# riassunto` o il sottotitolo in altre parti del file.

## Contenuto

Organizza il contenuto per argomenti reali emersi nella call. Per ogni topic:

- riportare cosa e' stato detto in modo asciutto ma specifico;
- mantenere esempi, numeri, nomi e vincoli quando presenti;
- distinguere decisioni, ipotesi, dubbi e prossimi passi;
- evitare formule vaghe come "si e' discusso di..." senza dettaglio operativo.

Usa sezioni `###` granulari, ad esempio:

### Contesto del progetto
### Autenticazione e permessi
### Decisioni
### Action item
### Domande aperte
### Citazioni rilevanti

Includi solo sezioni supportate dalla trascrizione. Se una call ruota attorno a temi diversi, crea titoli `###` coerenti con quei temi.

## Action item

Quando emergono task o follow-up, usa una tabella con queste colonne:

| Owner | Task | Scadenza | Stato/Dipendenza |
| --- | --- | --- | --- |

- Lascia `Owner` vuoto se non emerge dalla call.
- Lascia `Scadenza` vuota se non viene indicata.
- Usa `Stato/Dipendenza` per blocchi, prerequisiti, attese, verifiche o note operative.

## Citazioni

Inserisci citazioni testuali brevi solo quando sono utili:

- decisione netta;
- vincolo importante;
- frase ambigua da rileggere;
- formulazione tecnica significativa.

Non abusarne: poche citazioni mirate valgono piu' di molte frasi generiche.

## Criteri di qualita'

- Il riassunto deve essere fedele prima che elegante.
- Deve preservare il livello di dettaglio utile per ritrovare decisioni, numeri, nomi e passaggi tecnici.
- Deve essere leggibile in Obsidian grazie a frontmatter, sezioni granulari e tag.
