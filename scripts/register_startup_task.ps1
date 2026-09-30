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
$watchLauncher = Join-Path $PSScriptRoot 'start_watcher.cmd'

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Error "Python venv non trovato: $venvPython. Eseguire prima: python -m venv .venv && .venv\Scripts\pip install -e ."
    exit 1
}

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

# Action: il launcher imposta UTF-8 e lascia a settings.py il VAULT_ROOT da .env.
$action = New-ScheduledTaskAction `
    -Execute $watchLauncher `
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
Write-Host "Log: VAULT_ROOT\logs\watcher.log (VAULT_ROOT è impostato da .env)"
Write-Host ""
Write-Host "Comandi utili:"
Write-Host "  Avvia subito:   Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "  Ferma:          Stop-ScheduledTask  -TaskName '$TaskName'"
Write-Host "  Rimuovi:        Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
