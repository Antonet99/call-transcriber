function Get-DefaultSummaryModel {
    return 'claude-sonnet-4-6'
}

function Get-DefaultTaskModel {
    return 'claude-sonnet-4-6'
}

function Test-LlmProviderAvailable {
    return [bool](Get-Command claude -ErrorAction SilentlyContinue)
}

function New-ClaudeSummaryAgentsJson {
    $agents = [ordered]@{
        'call-metadata-auditor' = [ordered]@{
            description = 'Controlla metadati, persone, sistemi e tag del riassunto call prima della risposta finale.'
            prompt = 'Sei un revisore di metadati per riassunti di call in Obsidian. Verifica che frontmatter YAML, persone, sistemi e tag derivino dalla trascrizione, che il tag call sia presente e che i nomi non vengano inventati. Restituisci al main agent solo correzioni puntuali.'
            model = 'claude-haiku-4-5'
            effort = 'medium'
        }
        'call-action-auditor' = [ordered]@{
            description = 'Controlla decisioni, action item, dipendenze, numeri e citazioni rilevanti del riassunto call.'
            prompt = 'Sei un revisore di contenuto per riassunti di call. Controlla che decisioni, action item, owner, scadenze, dipendenze, numeri, date e citazioni brevi siano fedeli alla trascrizione. Segnala omissioni concrete al main agent senza riscrivere tutto il riassunto.'
            model = 'claude-haiku-4-5'
            effort = 'medium'
        }
    }

    return ($agents | ConvertTo-Json -Depth 6 -Compress)
}

function Add-ClaudeSummaryAgentInstructions {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt
    )

@"
$Prompt

---

Istruzioni Claude Code:
- Durante la preparazione del riassunto, usa se utile i subagent call-metadata-auditor e call-action-auditor come controllo interno.
- Integra solo le correzioni utili nel Markdown finale.
- Non riportare log, ragionamenti, output dei subagent o note operative.
- La risposta finale deve contenere esclusivamente il Markdown del riassunto richiesto.
"@
}

function Invoke-ClaudeText {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt,

        [Parameter(Mandatory = $true)]
        [string]$Model,

        [Parameter(Mandatory = $true)]
        [ValidateSet('low', 'medium', 'high', 'xhigh', 'max')]
        [string]$Effort,

        [Parameter(Mandatory = $false)]
        [string]$AgentsJson = ''
    )

    $claude = Get-Command claude -ErrorAction SilentlyContinue
    if (-not $claude) {
        throw 'Claude Code CLI non trovato nel PATH. Verifica che il comando claude sia disponibile.'
    }

    $args = @('-p', '--model', $Model, '--effort', $Effort, '--output-format', 'text')
    if (-not [string]::IsNullOrWhiteSpace($AgentsJson)) {
        $args += @('--agents', $AgentsJson)
    }

    $lines = @($Prompt | & $claude.Source @args)
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
    $promptWithAgentInstructions = Add-ClaudeSummaryAgentInstructions -Prompt $Prompt
    $agentsJson = New-ClaudeSummaryAgentsJson

    return Invoke-ClaudeText -Prompt $promptWithAgentInstructions -Model $Model -Effort $effort -AgentsJson $agentsJson
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
