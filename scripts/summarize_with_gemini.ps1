param(
    [Parameter(Mandatory = $true)]
    [string]$TranscriptPath,

    [Parameter(Mandatory = $false)]
    [string]$OutputPath,

    [Parameter(Mandatory = $false)]
    [string]$PromptPath = '',

    [Parameter(Mandatory = $false)]
    [string]$Model = ''
)

$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8

. (Join-Path $PSScriptRoot 'llm\common.ps1')
. (Join-Path $PSScriptRoot 'llm\providers\gemini.ps1')

Invoke-SummaryEntrypoint `
    -TranscriptPath $TranscriptPath `
    -OutputPath $OutputPath `
    -PromptPath $PromptPath `
    -Model $Model `
    -Provider 'gemini'
