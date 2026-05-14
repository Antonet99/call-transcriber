$Utf8NoBom = New-Object Text.UTF8Encoding($false)

function Resolve-LlmExistingFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $resolved = Resolve-Path -LiteralPath $Path -ErrorAction SilentlyContinue
    if (-not $resolved) {
        throw "$Label non trovato: $Path"
    }

    return $resolved.Path
}

function Get-LlmProviderPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptRoot,

        [Parameter(Mandatory = $true)]
        [ValidateSet('gemini', 'claude', 'codex')]
        [string]$Provider
    )

    return Join-Path $ScriptRoot "llm\providers\$Provider.ps1"
}

function Get-DefaultPromptPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptRoot,

        [Parameter(Mandatory = $false)]
        [string]$PromptPath = ''
    )

    if ([string]::IsNullOrWhiteSpace($PromptPath)) {
        return Join-Path $ScriptRoot 'prompt_riassunto_call.md'
    }

    return $PromptPath
}

function New-SummaryInputText {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PromptPath,

        [Parameter(Mandatory = $true)]
        [string]$TranscriptPath
    )

    $prompt = [IO.File]::ReadAllText($PromptPath, [Text.Encoding]::UTF8)
    $transcript = [IO.File]::ReadAllText($TranscriptPath, [Text.Encoding]::UTF8)

@"
$prompt

---

Trascrizione da riassumere:

$transcript
"@
}

function Convert-ToCleanMarkdown {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Text
    )

    $clean = $Text.Trim()

    if ($clean -match '(?s)```(?:md|markdown)?\s*(.*?)```') {
        $clean = $Matches[1].Trim()
    }

    $lines = @($clean -split '\r?\n' | Where-Object {
        $line = $_.Trim()
        $line -notmatch '^```' -and
        $line -notmatch '^Leggo la trascrizione e produco il riassunto\.?$'
    })

    $clean = (($lines -join [Environment]::NewLine).Trim())
    $frontmatterStart = [regex]::Match($clean, '(?ms)^---\s*$.*?^---\s*$\s*^#\s+riassunto\s*$')
    if ($frontmatterStart.Success -and $frontmatterStart.Index -gt 0) {
        $clean = $clean.Substring($frontmatterStart.Index).Trim()
    } else {
        $summaryStart = [regex]::Match($clean, '(?m)^#\s+riassunto\s*$')
        if ($summaryStart.Success -and $summaryStart.Index -gt 0) {
            $clean = $clean.Substring($summaryStart.Index).Trim()
        }
    }

    return $clean
}

function Assert-ValidSummary {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Text
    )

    if ($Text -match '(?i)in attesa di approvazione|approvazione per scrivere|scrivere il file') {
        throw 'Il provider LLM ha restituito una richiesta operativa invece del riassunto.'
    }

    $lines = @($Text -split '\r?\n')
    $firstContentIndex = $null
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if (-not [string]::IsNullOrWhiteSpace($lines[$i])) {
            $firstContentIndex = $i
            break
        }
    }

    if ($null -ne $firstContentIndex -and $lines[$firstContentIndex].Trim() -eq '---') {
        $frontmatterClosed = $false
        for ($i = $firstContentIndex + 1; $i -lt $lines.Count; $i++) {
            if ($lines[$i].Trim() -eq '---') {
                $frontmatterClosed = $true
                break
            }
        }

        if (-not $frontmatterClosed) {
            throw 'Il frontmatter YAML non e'' chiuso correttamente.'
        }
    }

    if ($Text -notmatch '(?m)^#\s+riassunto\s*$') {
        throw 'Il riassunto non contiene il titolo principale richiesto.'
    }

    if ($Text -notmatch '(?m)^##\s+\S') {
        throw 'Il riassunto non contiene il sottotitolo contestuale richiesto.'
    }

    if ($Text -notmatch '(?m)^###\s+\S') {
        throw 'Il riassunto non contiene sezioni di dettaglio.'
    }
}

function Write-LlmTextFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Text
    )

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    [IO.File]::WriteAllText($Path, $Text, $Utf8NoBom)
}

function Invoke-SummaryEntrypoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TranscriptPath,

        [Parameter(Mandatory = $false)]
        [string]$OutputPath = '',

        [Parameter(Mandatory = $false)]
        [string]$PromptPath = '',

        [Parameter(Mandatory = $false)]
        [string]$Model = '',

        [Parameter(Mandatory = $true)]
        [string]$Provider
    )

    $scriptRoot = if ($PSScriptRoot) { Split-Path -Parent $PSScriptRoot } else { Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
    $TranscriptPath = Resolve-LlmExistingFile -Path $TranscriptPath -Label 'Trascrizione'
    $PromptPath = Resolve-LlmExistingFile -Path (Get-DefaultPromptPath -ScriptRoot $scriptRoot -PromptPath $PromptPath) -Label 'Prompt'

    if ([string]::IsNullOrWhiteSpace($OutputPath)) {
        $OutputPath = Join-Path (Split-Path -Parent $TranscriptPath) 'riassunto.md'
    }

    if (-not (Test-LlmProviderAvailable)) {
        throw "Provider LLM non disponibile: $Provider"
    }

    if ([string]::IsNullOrWhiteSpace($Model)) {
        $Model = Get-DefaultSummaryModel
    }

    $inputText = New-SummaryInputText -PromptPath $PromptPath -TranscriptPath $TranscriptPath
    $summary = Invoke-SummaryGeneration -Prompt $inputText -Model $Model
    if (-not $summary) {
        throw "Il provider $Provider non ha restituito un riassunto."
    }

    $summary = Convert-ToCleanMarkdown -Text $summary
    if (-not $summary) {
        throw "Il provider $Provider non ha restituito Markdown valido."
    }

    Assert-ValidSummary -Text $summary
    Write-LlmTextFile -Path $OutputPath -Text $summary

    [pscustomobject]@{
        OutputPath = $OutputPath
        Provider = $Provider
        Model = $Model
    }
}

function New-TaskClassificationPrompt {
    param(
        [Parameter(Mandatory = $true)]
        [array]$Tasks,

        [Parameter(Mandatory = $true)]
        [string]$SummaryPath,

        [Parameter(Mandatory = $true)]
        [string]$Title
    )

    $taskList = ($Tasks | ForEach-Object { "- $($_.Name)" }) -join [Environment]::NewLine
    $summary = [IO.File]::ReadAllText($SummaryPath, [Text.Encoding]::UTF8)
    if ($summary.Length -gt 5000) {
        $summary = $summary.Substring(0, 5000)
    }

@"
Devi classificare una call già riassunta dentro una delle cartelle task esistenti.

Rispondi solo con il nome esatto di una cartella tra quelle elencate. Non aggiungere spiegazioni, virgolette, markdown o testo extra.

Cartelle task disponibili:
$taskList

Titolo call:
$Title

Riassunto call:
$summary
"@
}

function Select-TaskDirectoryFromAnswer {
    param(
        [Parameter(Mandatory = $true)]
        [array]$Tasks,

        [Parameter(Mandatory = $true)]
        [string]$Answer
    )

    $clean = ($Answer -replace '^["''`]+|["''`]+$', '').Trim()
    $clean = ($clean -replace '(?i)^```(?:text|markdown)?\s*', '' -replace '(?i)\s*```$', '').Trim()

    $selected = $Tasks | Where-Object { $_.Name -ieq $clean } | Select-Object -First 1
    if (-not $selected) {
        $selected = $Tasks | Where-Object { $clean -like "*$($_.Name)*" } | Select-Object -First 1
    }

    return $selected
}
