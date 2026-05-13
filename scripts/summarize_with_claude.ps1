param(
    [Parameter(Mandatory = $true)]
    [string]$TranscriptPath,

    [Parameter(Mandatory = $false)]
    [string]$OutputPath,

    [Parameter(Mandatory = $false)]
    [string]$PromptPath = '',

    [Parameter(Mandatory = $false)]
    [string]$Model = 'claude-sonnet-4-6',

    [Parameter(Mandatory = $false)]
    [ValidateSet('low', 'medium', 'high', 'xhigh', 'max')]
    [string]$Effort = 'medium'
)

$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8
$Utf8NoBom = New-Object Text.UTF8Encoding($false)

if ([string]::IsNullOrWhiteSpace($PromptPath)) {
    $scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $PromptPath = Join-Path $scriptDir 'prompt_riassunto_call.md'
}

function Resolve-ExistingFile {
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

    return (($lines -join [Environment]::NewLine).Trim())
}

function Assert-ValidSummary {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Text
    )

    if ($Text -match '(?i)in attesa di approvazione|approvazione per scrivere|scrivere il file') {
        throw 'Claude ha restituito una richiesta operativa invece del riassunto.'
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

$TranscriptPath = Resolve-ExistingFile -Path $TranscriptPath -Label 'Trascrizione'
$PromptPath = Resolve-ExistingFile -Path $PromptPath -Label 'Prompt'

if (-not $OutputPath) {
    $OutputPath = Join-Path (Split-Path -Parent $TranscriptPath) 'riassunto.md'
}

$claude = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claude) {
    throw 'Claude Code CLI non trovato nel PATH. Verifica che il comando claude sia disponibile.'
}

$prompt = [IO.File]::ReadAllText($PromptPath, [Text.Encoding]::UTF8)
$transcript = [IO.File]::ReadAllText($TranscriptPath, [Text.Encoding]::UTF8)
$inputText = @"
$prompt

---

Trascrizione da riassumere:

$transcript
"@

$summaryLines = @($inputText | & $claude.Source -p --model $Model --effort $Effort --output-format text)
$summary = ($summaryLines -join [Environment]::NewLine).Trim()

if (-not $summary) {
    throw 'Claude non ha restituito un riassunto.'
}

$summary = Convert-ToCleanMarkdown -Text $summary
if (-not $summary) {
    throw 'Claude non ha restituito Markdown valido.'
}
Assert-ValidSummary -Text $summary

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputPath) | Out-Null
[IO.File]::WriteAllText($OutputPath, $summary, $Utf8NoBom)

[pscustomobject]@{
    OutputPath = $OutputPath
    Model = $Model
    Effort = $Effort
}
