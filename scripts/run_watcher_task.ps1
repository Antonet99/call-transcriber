$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8

$rootPath = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $rootPath 'logs'
$logPath = Join-Path $logDir 'watcher.log'

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-Log {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    $line = '{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $Message
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

try {
    Write-Log 'Watcher task avviato.'
    & (Join-Path $PSScriptRoot 'watch_calls.ps1') -RootPath $rootPath
} catch {
    Write-Log "Errore: $($_.Exception.Message)"
    throw
}
