function Get-DefaultSummaryModel {
    return 'gemini-3.1-pro-preview'
}

function Get-DefaultTaskModel {
    return 'gemini-3-flash-preview'
}

function Test-LlmProviderAvailable {
    return [bool](Get-Command gemini -ErrorAction SilentlyContinue)
}

function Invoke-GeminiText {
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
        $lines = @($Prompt | & $gemini.Source -p ' ' --model $Model --output-format text --skip-trust)
        if ($LASTEXITCODE -ne 0) {
            throw "Gemini CLI ha restituito codice di uscita $LASTEXITCODE."
        }
    } finally {
        $env:TERM = $oldTerm
        $env:COLORTERM = $oldColorTerm
    }

    return (($lines -join [Environment]::NewLine).Trim())
}

function Add-GeminiSummaryInstructions {
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

function Invoke-SummaryGeneration {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt,

        [Parameter(Mandatory = $true)]
        [string]$Model
    )

    $promptWithGeminiInstructions = Add-GeminiSummaryInstructions -Prompt $Prompt
    return Invoke-GeminiText -Prompt $promptWithGeminiInstructions -Model $Model
}

function Invoke-TaskClassification {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt,

        [Parameter(Mandatory = $true)]
        [string]$Model
    )

    return Invoke-GeminiText -Prompt $Prompt -Model $Model
}
