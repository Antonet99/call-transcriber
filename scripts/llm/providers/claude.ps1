function Get-DefaultSummaryModel {
    return 'claude-sonnet-4-6'
}

function Get-DefaultTaskModel {
    return 'claude-sonnet-4-6'
}

function Test-LlmProviderAvailable {
    return [bool](Get-Command claude -ErrorAction SilentlyContinue)
}

function Invoke-ClaudeText {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt,

        [Parameter(Mandatory = $true)]
        [string]$Model,

        [Parameter(Mandatory = $true)]
        [ValidateSet('low', 'medium', 'high', 'xhigh', 'max')]
        [string]$Effort
    )

    $claude = Get-Command claude -ErrorAction SilentlyContinue
    if (-not $claude) {
        throw 'Claude Code CLI non trovato nel PATH. Verifica che il comando claude sia disponibile.'
    }

    $lines = @($Prompt | & $claude.Source -p --model $Model --effort $Effort --output-format text)
    if ($LASTEXITCODE -ne 0) {
        throw "Claude CLI ha restituito codice di uscita $LASTEXITCODE."
    }

    return (($lines -join [Environment]::NewLine).Trim())
}

function Invoke-SummaryGeneration {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt,

        [Parameter(Mandatory = $true)]
        [string]$Model
    )

    $effort = if ($script:ClaudeSummaryEffort) { $script:ClaudeSummaryEffort } else { 'high' }
    return Invoke-ClaudeText -Prompt $Prompt -Model $Model -Effort $effort
}

function Invoke-TaskClassification {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt,

        [Parameter(Mandatory = $true)]
        [string]$Model
    )

    return Invoke-ClaudeText -Prompt $Prompt -Model $Model -Effort low
}
