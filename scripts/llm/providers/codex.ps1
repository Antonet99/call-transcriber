function Get-DefaultSummaryModel {
    return ''
}

function Get-DefaultTaskModel {
    return ''
}

function Test-LlmProviderAvailable {
    return $true
}

function Invoke-SummaryGeneration {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt,

        [Parameter(Mandatory = $false)]
        [string]$Model = ''
    )

    throw 'Provider Codex non ancora implementato: definire prima CLI/API e modalita'' headless.'
}

function Invoke-TaskClassification {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt,

        [Parameter(Mandatory = $false)]
        [string]$Model = ''
    )

    throw 'Provider Codex non ancora implementato: definire prima CLI/API e modalita'' headless.'
}
