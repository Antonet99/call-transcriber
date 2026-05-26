# Registra il watcher Python come Task Scheduler al login dell'utente corrente.
# Eseguire una sola volta (o ogni volta che si vuole aggiornare il task).
# Richiede PowerShell con privilegi normali (non richiede Admin).

param(
    [Parameter(Mandatory = $false)]
    [string]$TaskName = 'CallWatcher',

    [Parameter(Mandatory = $false)]
    [string[]]$LegacyTaskNames = @('Call Automation Watcher')
)

$rootDir = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $rootDir '.venv\Scripts\python.exe'
$watchScript = Join-Path $PSScriptRoot 'watch_calls.py'
$logDir = Join-Path $rootDir 'logs'
$logFile = Join-Path $logDir 'watcher.log'

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Error "Python venv non trovato: $venvPython. Eseguire prima: python -m venv .venv && .venv\Scripts\pip install -e ."
    exit 1
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

foreach ($legacyTaskName in $LegacyTaskNames) {
    if ($legacyTaskName -eq $TaskName) {
        continue
    }

    $legacyTask = Get-ScheduledTask -TaskName $legacyTaskName -ErrorAction SilentlyContinue
    if ($legacyTask) {
        Stop-ScheduledTask -TaskName $legacyTaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $legacyTaskName -Confirm:$false
        Write-Host "Task legacy rimosso: $legacyTaskName"
    }
}

# Action: il watcher scrive gia' su logs/watcher.log tramite logging Python.
$action = New-ScheduledTaskAction `
    -Execute $venvPython `
    -Argument "`"$watchScript`" --root-path `"$rootDir`"" `
    -WorkingDirectory $rootDir

# Trigger: al login dell'utente corrente
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# Settings: riavvio automatico se crasha, non avviare se già in esecuzione
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([System.TimeSpan]::Zero)

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "Task '$TaskName' registrato. Il watcher si avviera' automaticamente al prossimo login."
Write-Host "Log: $logFile"
Write-Host ""
Write-Host "Comandi utili:"
Write-Host "  Avvia subito:   Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "  Ferma:          Stop-ScheduledTask  -TaskName '$TaskName'"
Write-Host "  Rimuovi:        Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
