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

function Get-SafeName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $invalid = [IO.Path]::GetInvalidFileNameChars()
    $safe = -join ($Name.ToCharArray() | ForEach-Object {
        if ($invalid -contains $_) { '-' } else { $_ }
    })

    return ($safe -replace '\s+', ' ').Trim()
}

function Get-SummaryFileName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Title
    )

    return (Get-SafeName -Name $Title) + '.md'
}

function Get-CallTitleFromDirectoryName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DirectoryName
    )

    if ($DirectoryName -match '^(\d{4}-\d{2}-\d{2})\s+(\d{2})\.(\d{2})\s+-\s+(.+)$') {
        return [pscustomobject]@{
            Date = $Matches[1]
            Time = "$($Matches[2]):$($Matches[3])"
            Title = $Matches[4].Trim()
        }
    }

    return [pscustomobject]@{
        Date = ''
        Time = ''
        Title = $DirectoryName.Trim()
    }
}

function Test-PersonNameSegment {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    $parts = @($Value -split '\s+(?:e|and)\s+|&|/' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($parts.Count -eq 0) {
        return $false
    }

    foreach ($part in $parts) {
        $words = @($part -split '\s+' | Where-Object { $_ })
        if ($words.Count -gt 2) {
            return $false
        }

        foreach ($word in $words) {
            if ($word -cmatch '^[A-Z0-9_]{2,}$') {
                return $false
            }

            if ($word -notmatch '^[A-ZÀ-Ý][a-zà-ÿ''-]+$') {
                return $false
            }
        }
    }

    return $true
}

function Get-PeopleFromTitle {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Title
    )

    if ($Title -notmatch '^([^,]+),\s+.+$') {
        return @()
    }

    $prefix = $Matches[1].Trim()
    if (-not (Test-PersonNameSegment -Value $prefix)) {
        return @()
    }

    return @($prefix -split '\s+(?:e|and)\s+|&|/' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Convert-ToKebabTag {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    return ($Value.ToLowerInvariant() -replace '[^a-z0-9]+', '-' -replace '^-+|-+$', '').Trim()
}

function Get-CallInfo {
    param(
        [Parameter(Mandatory = $true)]
        [IO.DirectoryInfo]$Directory,

        [Parameter(Mandatory = $true)]
        [string]$TaskName
    )

    $titleInfo = Get-CallTitleFromDirectoryName -DirectoryName $Directory.Name
    $summaryPath = Sync-CallSummaryFile -Directory $Directory -Title $titleInfo.Title
    if (-not $summaryPath) {
        return $null
    }

    Sync-SummaryPeopleFromTitle -SummaryPath $summaryPath -Title $titleInfo.Title

    [pscustomobject]@{
        Task = $TaskName
        Directory = $Directory
        Date = $titleInfo.Date
        Time = $titleInfo.Time
        Title = $titleInfo.Title
        SummaryPath = $summaryPath
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

function Find-SummaryMarkdownPath {
    param(
        [Parameter(Mandatory = $true)]
        [IO.DirectoryInfo]$Directory,

        [Parameter(Mandatory = $true)]
        [string]$Title
    )

    $expected = Join-Path $Directory.FullName (Get-SummaryFileName -Title $Title)
    if (Test-Path -LiteralPath $expected) {
        return $expected
    }

    $legacy = Join-Path $Directory.FullName 'riassunto.md'
    if (Test-Path -LiteralPath $legacy) {
        return $legacy
    }

    $markdown = Get-ChildItem -LiteralPath $Directory.FullName -File -Filter '*.md' -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne 'README.md' } |
        Select-Object -First 1

    if ($markdown) {
        return $markdown.FullName
    }

    return ''
}

function Sync-CallSummaryFile {
    param(
        [Parameter(Mandatory = $true)]
        [IO.DirectoryInfo]$Directory,

        [Parameter(Mandatory = $true)]
        [string]$Title
    )

    $source = Find-SummaryMarkdownPath -Directory $Directory -Title $Title
    if (-not $source) {
        return ''
    }

    $target = Join-Path $Directory.FullName (Get-SummaryFileName -Title $Title)
    if (-not [string]::Equals($source, $target, [StringComparison]::OrdinalIgnoreCase)) {
        Move-Item -LiteralPath $source -Destination $target -Force
    }

    return $target
}

function Sync-SummaryPeopleFromTitle {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SummaryPath,

        [Parameter(Mandatory = $true)]
        [string]$Title
    )

    $peopleFromTitle = @(Get-PeopleFromTitle -Title $Title)
    if ($peopleFromTitle.Count -eq 0) {
        return
    }

    $content = [IO.File]::ReadAllText($SummaryPath, [Text.Encoding]::UTF8).Trim()
    $lines = @($content -split '\r?\n')
    $frontmatterStart = 0
    while ($frontmatterStart -lt $lines.Count -and [string]::IsNullOrWhiteSpace($lines[$frontmatterStart])) {
        $frontmatterStart++
    }

    $frontmatterEnd = -1
    if ($frontmatterStart -lt $lines.Count -and $lines[$frontmatterStart].Trim() -eq '---') {
        for ($i = $frontmatterStart + 1; $i -lt $lines.Count; $i++) {
            if ($lines[$i].Trim() -eq '---') {
                $frontmatterEnd = $i
                break
            }
        }
    }

    if ($frontmatterEnd -lt 0) {
        $newFrontmatter = @(
            '---',
            ('persone: [' + ($peopleFromTitle -join ', ') + ']'),
            ('tags: [' + ((@('call') + @($peopleFromTitle | ForEach-Object { Convert-ToKebabTag -Value $_ })) -join ', ') + ']'),
            '---',
            ''
        )
        [IO.File]::WriteAllText($SummaryPath, (($newFrontmatter + $lines) -join [Environment]::NewLine).Trim() + [Environment]::NewLine, $Utf8NoBom)
        return
    }

    $metadata = @()
    $existingPeople = @()
    $existingTags = @()

    for ($i = $frontmatterStart + 1; $i -lt $frontmatterEnd; $i++) {
        $line = $lines[$i]
        if ($line -match '^\s*persone\s*:\s*(.*?)\s*$') {
            if (-not [string]::IsNullOrWhiteSpace($Matches[1])) {
                $existingPeople = @(Convert-FrontmatterArray -Value $Matches[1])
            } else {
                while (($i + 1) -lt $frontmatterEnd -and $lines[$i + 1] -match '^\s*-\s*(.+?)\s*$') {
                    $existingPeople += $Matches[1].Trim()
                    $i++
                }
            }
            continue
        }
        if ($line -match '^\s*tags\s*:\s*(.*?)\s*$') {
            if (-not [string]::IsNullOrWhiteSpace($Matches[1])) {
                $existingTags = @(Convert-FrontmatterArray -Value $Matches[1])
            } else {
                while (($i + 1) -lt $frontmatterEnd -and $lines[$i + 1] -match '^\s*-\s*(.+?)\s*$') {
                    $existingTags += $Matches[1].Trim()
                    $i++
                }
            }
            continue
        }
        $metadata += $line
    }

    $people = @($existingPeople + $peopleFromTitle | Where-Object { $_ } | Select-Object -Unique)
    $tags = @($existingTags + 'call' + @($peopleFromTitle | ForEach-Object { Convert-ToKebabTag -Value $_ }) | Where-Object { $_ } | Select-Object -Unique)
    $bodyStart = $frontmatterEnd + 1
    $body = if ($bodyStart -lt $lines.Count) { @($lines[$bodyStart..($lines.Count - 1)]) } else { @() }

    $newLines = @('---') + $metadata + @(
        ('persone: [' + ($people -join ', ') + ']'),
        ('tags: [' + ($tags -join ', ') + ']'),
        '---'
    ) + $body

    [IO.File]::WriteAllText($SummaryPath, (($newLines -join [Environment]::NewLine).Trim() + [Environment]::NewLine), $Utf8NoBom)
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

        if ($line -match '^\s*(persone|tags)\s*:\s*(.*?)\s*$') {
            $key = $Matches[1]
            $value = $Matches[2]
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                $metadata[$key] = Convert-FrontmatterArray -Value $value
                continue
            }

            $items = @()
            for ($j = $i + 1; $j -lt $lines.Count; $j++) {
                if ($lines[$j].Trim() -eq '---') {
                    break
                }
                if ($lines[$j] -match '^\s*-\s*(.+?)\s*$') {
                    $items += $Matches[1].Trim()
                    continue
                }
                break
            }

            $metadata[$key] = $items
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
        Sort-Object Name -Descending |
        ForEach-Object { Get-CallInfo -Directory $_ -TaskName $task.Name } |
        Where-Object { $null -ne $_ })

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
            $summaryName = [IO.Path]::GetFileNameWithoutExtension($call.SummaryPath)
            $target = Convert-ToWikiPath -Path (Join-Path $call.Directory.Name $summaryName)
            $alias = if ($call.Date) { "$($call.Date) - $($call.Title)" } else { $call.Title }
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
        $globalLines += "- [[$target|$($task.Name)]] - $count call"
    }
} else {
    $globalLines += '- Nessuna task presente.'
}

$globalLines += ''
$globalLines += '## Ultime 10 call'

$latestCalls = @($allCalls | Sort-Object @{ Expression = { $_.Directory.Name }; Descending = $true } | Select-Object -First 10)
if ($latestCalls.Count -gt 0) {
    foreach ($call in $latestCalls) {
        $summaryName = [IO.Path]::GetFileNameWithoutExtension($call.SummaryPath)
        $target = Convert-ToWikiPath -Path (Join-Path (Join-Path (Join-Path 'Task' $call.Task) $call.Directory.Name) $summaryName)
        $dateTime = if ($call.Date) { "$($call.Date) $($call.Time)" } else { $call.Directory.Name }
        $globalLines += "- $dateTime - [[$target|$($call.Title)]] (task: $($call.Task))"
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
