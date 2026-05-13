param(
    [Parameter(Mandatory = $false)]
    [string]$RootPath = ''
)

$ErrorActionPreference = 'Stop'
$Utf8NoBom = New-Object Text.UTF8Encoding($false)

if ([string]::IsNullOrWhiteSpace($RootPath)) {
    $scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $RootPath = Split-Path -Parent $scriptDir
}

$RootPath = (Resolve-Path -LiteralPath $RootPath).Path
$completedRoot = Join-Path $RootPath 'completate'
$taskRoot = Join-Path $completedRoot 'Task'

function Convert-ToWikiPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    return ($Path -replace '\\', '/')
}

function Get-CallInfo {
    param(
        [Parameter(Mandatory = $true)]
        [IO.DirectoryInfo]$Directory,

        [Parameter(Mandatory = $true)]
        [string]$TaskName
    )

    $date = ''
    $time = ''
    $title = $Directory.Name

    if ($Directory.Name -match '^(\d{4}-\d{2}-\d{2})\s+(\d{2})\.(\d{2})\s+-\s+(.+)$') {
        $date = $Matches[1]
        $time = "$($Matches[2]):$($Matches[3])"
        $title = $Matches[4]
    }

    [pscustomobject]@{
        Task = $TaskName
        Directory = $Directory
        Date = $date
        Time = $time
        Title = $title
        SummaryPath = Join-Path $Directory.FullName 'riassunto.md'
    }
}

function Convert-FrontmatterArray {
    param(
        [Parameter(Mandatory = $false)]
        [string]$Value = ''
    )

    $clean = $Value.Trim()
    if ($clean.StartsWith('[') -and $clean.EndsWith(']')) {
        $clean = $clean.Substring(1, $clean.Length - 2)
    }

    return @($clean -split ',' |
        ForEach-Object { ($_.Trim() -replace '^["'']|["'']$', '') } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
}

function Get-SummaryMetadata {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SummaryPath
    )

    $metadata = [ordered]@{
        persone = @()
        tags = @()
    }

    if (-not (Test-Path -LiteralPath $SummaryPath)) {
        return [pscustomobject]$metadata
    }

    $lines = [IO.File]::ReadAllLines($SummaryPath, [Text.Encoding]::UTF8)
    if ($lines.Count -eq 0 -or $lines[0].Trim() -ne '---') {
        return [pscustomobject]$metadata
    }

    for ($i = 1; $i -lt $lines.Count; $i++) {
        $line = $lines[$i]
        if ($line.Trim() -eq '---') {
            break
        }

        if ($line -match '^\s*(persone|tags)\s*:\s*(.+?)\s*$') {
            $metadata[$Matches[1]] = Convert-FrontmatterArray -Value $Matches[2]
        }
    }

    [pscustomobject]$metadata
}

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string[]]$Lines
    )

    $content = (($Lines -join [Environment]::NewLine).TrimEnd() + [Environment]::NewLine)
    [IO.File]::WriteAllText($Path, $content, $Utf8NoBom)
}

New-Item -ItemType Directory -Force -Path $completedRoot | Out-Null
New-Item -ItemType Directory -Force -Path $taskRoot | Out-Null

$tasks = @(Get-ChildItem -LiteralPath $taskRoot -Directory | Sort-Object Name)
$allCalls = @()

foreach ($task in $tasks) {
    $calls = @(Get-ChildItem -LiteralPath $task.FullName -Directory |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'riassunto.md') } |
        Sort-Object Name -Descending |
        ForEach-Object { Get-CallInfo -Directory $_ -TaskName $task.Name })

    $allCalls += $calls
    $people = @()
    $tags = @()

    foreach ($call in $calls) {
        $metadata = Get-SummaryMetadata -SummaryPath $call.SummaryPath
        $people += $metadata.persone
        $tags += $metadata.tags
    }

    $people = @($people | Where-Object { $_ } | Sort-Object -Unique)
    $tags = @($tags | Where-Object { $_ } | Sort-Object -Unique)
    $lines = @("# $($task.Name)", '')

    if ($people.Count -gt 0 -or $tags.Count -gt 0) {
        $lines += '## Riepilogo'
        if ($people.Count -gt 0) {
            $lines += ('- Persone: ' + ($people -join ', '))
        }
        if ($tags.Count -gt 0) {
            $lines += ('- Tag: ' + ($tags -join ', '))
        }
        $lines += ''
    }

    $lines += "## Call ($($calls.Count))"
    if ($calls.Count -gt 0) {
        foreach ($call in $calls) {
            $target = Convert-ToWikiPath -Path (Join-Path $call.Directory.Name 'riassunto')
            $alias = if ($call.Date) { "$($call.Date) — $($call.Title)" } else { $call.Title }
            $lines += "- [[$target|$alias]]"
        }
    }

    Write-Utf8NoBom -Path (Join-Path $task.FullName 'README.md') -Lines $lines
}

$globalLines = @('# Knowledge base call', '', '## Task attive')

if ($tasks.Count -gt 0) {
    foreach ($task in $tasks) {
        $count = @($allCalls | Where-Object { $_.Task -eq $task.Name }).Count
        $target = Convert-ToWikiPath -Path (Join-Path (Join-Path 'Task' $task.Name) 'README')
        $globalLines += "- [[$target|$($task.Name)]] — $count call"
    }
} else {
    $globalLines += '- Nessuna task presente.'
}

$globalLines += ''
$globalLines += '## Ultime 10 call'

$latestCalls = @($allCalls | Sort-Object @{ Expression = { $_.Directory.Name }; Descending = $true } | Select-Object -First 10)
if ($latestCalls.Count -gt 0) {
    foreach ($call in $latestCalls) {
        $target = Convert-ToWikiPath -Path (Join-Path (Join-Path (Join-Path 'Task' $call.Task) $call.Directory.Name) 'riassunto')
        $dateTime = if ($call.Date) { "$($call.Date) $($call.Time)" } else { $call.Directory.Name }
        $globalLines += "- $dateTime — [[$target|$($call.Title)]] (task: $($call.Task))"
    }
} else {
    $globalLines += '- Nessuna call archiviata.'
}

Write-Utf8NoBom -Path (Join-Path $completedRoot 'README.md') -Lines $globalLines

[pscustomobject]@{
    GlobalIndex = Join-Path $completedRoot 'README.md'
    TaskIndexes = $tasks.Count
    Calls = $allCalls.Count
}
