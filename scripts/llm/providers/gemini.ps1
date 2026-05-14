function script:Get-DefaultSummaryModel {
    return 'gemini-3.1-pro-preview'
}

function script:Get-DefaultTaskModel {
    return 'gemini-3-flash-preview'
}

function script:Test-LlmProviderAvailable {
    return [bool](Get-Command gemini -ErrorAction SilentlyContinue)
}

function script:Invoke-GeminiText {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt,

        [Parameter(Mandatory = $true)]
        [string]$Model
    )

    $gemini = Get-Command gemini -ErrorAction SilentlyContinue
    if (-not $gemini) {
        throw 'Gemini CLI non trovato nel PATH. Verifica che il comando gemini sia disponibile.'
    }

    $oldTerm = $env:TERM
    $oldColorTerm = $env:COLORTERM
    $env:TERM = 'xterm-256color'
    $env:COLORTERM = 'truecolor'

    try {
        $lines = @($Prompt | & $gemini.Source -p ' ' --model $Model --output-format text --skip-trust 2>&1)
        if ($LASTEXITCODE -ne 0) {
            $detail = (($lines | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine).Trim()
            if ([string]::IsNullOrWhiteSpace($detail)) {
                throw "Gemini CLI ha restituito codice di uscita $LASTEXITCODE."
            }

            throw "Gemini CLI ha restituito codice di uscita $LASTEXITCODE. Dettaglio: $detail"
        }
    } finally {
        $env:TERM = $oldTerm
        $env:COLORTERM = $oldColorTerm
    }

    return (($lines | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine).Trim()
}

function script:Add-GeminiSummaryInstructions {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt
    )

    return $Prompt + @'

---

Istruzioni specifiche Gemini CLI:

- Se disponibili, usa i subagent `@call_metadata_auditor` e `@call_action_auditor` come controllo interno prima della risposta finale.
- `@call_metadata_auditor` deve verificare persone, sistemi, tag e frontmatter.
- `@call_action_auditor` deve verificare decisioni, action item, dipendenze, domande aperte e citazioni rilevanti.
- Non riportare log, ragionamenti, risultati dei subagent o note operative.
- La risposta finale deve restare solo il Markdown del `riassunto.md`, nel formato richiesto dal prompt principale.
'@
}

function script:Invoke-SummaryGeneration {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt,

        [Parameter(Mandatory = $true)]
        [string]$Model
    )

    $promptWithGeminiInstructions = Add-GeminiSummaryInstructions -Prompt $Prompt
    return Invoke-GeminiText -Prompt $promptWithGeminiInstructions -Model $Model
}

function script:Invoke-TaskClassification {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt,

        [Parameter(Mandatory = $true)]
        [string]$Model
    )

    return Invoke-GeminiText -Prompt $Prompt -Model $Model
}
