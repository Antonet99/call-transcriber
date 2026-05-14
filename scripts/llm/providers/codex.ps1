function script:Get-DefaultSummaryModel {
    return ''
}

function script:Get-DefaultTaskModel {
    return ''
}

function script:Test-LlmProviderAvailable {
    return $true
}

function script:Invoke-SummaryGeneration {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt,

        [Parameter(Mandatory = $false)]
        [string]$Model = ''
    )

    throw 'Provider Codex non ancora implementato: definire prima CLI/API e modalita'' headless.'
}

function script:Invoke-TaskClassification {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt,

        [Parameter(Mandatory = $false)]
        [string]$Model = ''
    )

    throw 'Provider Codex non ancora implementato: definire prima CLI/API e modalita'' headless.'
}
