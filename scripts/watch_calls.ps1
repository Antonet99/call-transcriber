param(
    [Parameter(Mandatory = $false)]
    [string]$RootPath = ''
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($RootPath)) {
    $scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $RootPath = Split-Path -Parent $scriptDir
}

$RootPath = (Resolve-Path -LiteralPath $RootPath).Path
$watchPath = Join-Path $RootPath 'da_processare'
$processScript = Join-Path $PSScriptRoot 'process_call.ps1'
$supportedExtensions = @('.m4a', '.mp3', '.wav', '.aac', '.flac', '.ogg', '.webm', '.wma', '.mp4', '.mkv', '.mov', '.avi')
$processed = New-Object 'System.Collections.Generic.HashSet[string]'

New-Item -ItemType Directory -Force -Path $watchPath | Out-Null

function Invoke-ProcessFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    $item = Get-Item -LiteralPath $Path
    if ($item.PSIsContainer) {
        return
    }

    if ($supportedExtensions -notcontains $item.Extension.ToLowerInvariant()) {
        return
    }

    if (-not $processed.Add($item.FullName)) {
        return
    }

    try {
        Write-Host "Processo: $($item.FullName)"
        & $processScript -InputPath $item.FullName -RootPath $RootPath
        Write-Host "Completato: $($item.Name)"
    } catch {
        Write-Host "Errore su $($item.Name): $($_.Exception.Message)" -ForegroundColor Red
        $processed.Remove($item.FullName) | Out-Null
    }
}

Get-ChildItem -LiteralPath $watchPath -File | ForEach-Object {
    Invoke-ProcessFile -Path $_.FullName
}

$watcher = New-Object IO.FileSystemWatcher
$watcher.Path = $watchPath
$watcher.Filter = '*.*'
$watcher.IncludeSubdirectories = $false
$watcher.EnableRaisingEvents = $true

Register-ObjectEvent -InputObject $watcher -EventName Created -SourceIdentifier CallFileCreated | Out-Null
Register-ObjectEvent -InputObject $watcher -EventName Renamed -SourceIdentifier CallFileRenamed | Out-Null

Write-Host "Watcher attivo su: $watchPath"
Write-Host 'Premi Ctrl+C per fermarlo.'

try {
    while ($true) {
        $event = Wait-Event -Timeout 2
        if ($event) {
            $path = $event.SourceEventArgs.FullPath
            Remove-Event -EventIdentifier $event.EventIdentifier
            Start-Sleep -Seconds 2
            Invoke-ProcessFile -Path $path
        }
    }
} finally {
    Unregister-Event -SourceIdentifier CallFileCreated -ErrorAction SilentlyContinue
    Unregister-Event -SourceIdentifier CallFileRenamed -ErrorAction SilentlyContinue
    $watcher.Dispose()
}
