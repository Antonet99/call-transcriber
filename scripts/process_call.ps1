param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,

    [Parameter(Mandatory = $false)]
    [string]$RootPath = '',

    [Parameter(Mandatory = $false)]
    [switch]$KeepVideo,

    [Parameter(Mandatory = $false)]
    [decimal]$ArchiveMaxMB = 19
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($RootPath)) {
    $scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $RootPath = Split-Path -Parent $scriptDir
}

$RootPath = (Resolve-Path -LiteralPath $RootPath).Path

function Resolve-Tool {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    $wingetRoot = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path -LiteralPath $wingetRoot) {
        $match = Get-ChildItem -LiteralPath $wingetRoot -Recurse -Filter "$Name.exe" -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($match) {
            return $match.FullName
        }
    }

    throw "$Name non trovato."
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

function Get-ShortTitle {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Title
    )

    $clean = ($Title -replace '[#*_`]', '' -replace '\s+', ' ').Trim()
    $words = @($clean -split '\s+' | Where-Object { $_ })

    if ($words.Count -gt 6) {
        $clean = ($words | Select-Object -First 6) -join ' '
    }

    return Get-SafeName -Name $clean
}

function Get-SummaryTitle {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SummaryPath
    )

    $lines = [IO.File]::ReadAllLines($SummaryPath, [Text.Encoding]::UTF8)
    $genericHeadings = @(
        'contesto',
        'decisioni prese',
        'punti discussi',
        'task e action item',
        'blocchi, dubbi o rischi',
        'prossimi passi',
        'passaggi ambigui da verificare'
    )

    foreach ($line in $lines) {
        if ($line -match '^##\s+(.+?)\s*$') {
            $title = Get-ShortTitle -Title $Matches[1]
            if ($title -and ($genericHeadings -notcontains $title.ToLowerInvariant())) {
                return $title
            }
        }
    }

    return ''
}

function Set-SummaryTitle {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SummaryPath,

        [Parameter(Mandatory = $true)]
        [string]$Title
    )

    $content = [IO.File]::ReadAllText($SummaryPath, [Text.Encoding]::UTF8).Trim()
    $lines = @($content -split '\r?\n')

    if ($lines.Count -gt 0 -and $lines[0] -match '^#\s+') {
        $body = if ($lines.Count -gt 1) { @($lines[1..($lines.Count - 1)]) } else { @() }

        while ($body.Count -gt 0 -and [string]::IsNullOrWhiteSpace($body[0])) {
            $body = if ($body.Count -gt 1) { @($body[1..($body.Count - 1)]) } else { @() }
        }

        if ($body.Count -gt 0 -and $body[0] -match '^##\s+') {
            $body = if ($body.Count -gt 1) { @($body[1..($body.Count - 1)]) } else { @() }
        }

        $lines = @('# riassunto', "## $Title", '') + $body
    } else {
        $lines = @('# riassunto', "## $Title", '') + $lines
    }

    [IO.File]::WriteAllText($SummaryPath, (($lines -join [Environment]::NewLine).Trim() + [Environment]::NewLine), (New-Object Text.UTF8Encoding($false)))
}

function Get-UniqueDirectoryPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return $Path
    }

    $parent = Split-Path -Parent $Path
    $name = Split-Path -Leaf $Path
    $index = 2

    do {
        $candidate = Join-Path $parent "$name ($index)"
        $index++
    } while (Test-Path -LiteralPath $candidate)

    return $candidate
}

function Get-CallTaskDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RootPath,

        [Parameter(Mandatory = $true)]
        [string]$SummaryPath,

        [Parameter(Mandatory = $true)]
        [string]$Title
    )

    $completedRoot = Join-Path $RootPath 'completate'
    $taskRoot = Join-Path $completedRoot 'Task'

    if (-not (Test-Path -LiteralPath $taskRoot)) {
        return $completedRoot
    }

    $tasks = @(Get-ChildItem -LiteralPath $taskRoot -Directory | Sort-Object Name)
    if ($tasks.Count -eq 0) {
        return $taskRoot
    }

    $claude = Get-Command claude -ErrorAction SilentlyContinue
    if (-not $claude) {
        return $taskRoot
    }

    $taskList = ($tasks | ForEach-Object { "- $($_.Name)" }) -join [Environment]::NewLine
    $summary = [IO.File]::ReadAllText($SummaryPath, [Text.Encoding]::UTF8)
    if ($summary.Length -gt 5000) {
        $summary = $summary.Substring(0, 5000)
    }

    $prompt = @"
Devi classificare una call già riassunta dentro una delle cartelle task esistenti.

Rispondi solo con il nome esatto di una cartella tra quelle elencate. Non aggiungere spiegazioni, virgolette, markdown o testo extra.

Cartelle task disponibili:
$taskList

Titolo call:
$Title

Riassunto call:
$summary
"@

    $answer = (($prompt | & $claude.Source -p --model 'claude-sonnet-4-6' --effort low --output-format text) -join [Environment]::NewLine).Trim()
    $answer = ($answer -replace '^["''`]+|["''`]+$', '').Trim()

    $selected = $tasks | Where-Object { $_.Name -ieq $answer } | Select-Object -First 1
    if (-not $selected) {
        $selected = $tasks | Where-Object { $answer -like "*$($_.Name)*" } | Select-Object -First 1
    }

    if ($selected) {
        return $selected.FullName
    }

    return $taskRoot
}

function Wait-FileStable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $false)]
        [int]$StableChecks = 3,

        [Parameter(Mandatory = $false)]
        [int]$DelaySeconds = 3
    )

    $same = 0
    $lastLength = -1
    $lastWrite = [datetime]::MinValue

    while ($same -lt $StableChecks) {
        Start-Sleep -Seconds $DelaySeconds
        $item = Get-Item -LiteralPath $Path -ErrorAction Stop

        if ($item.Length -eq $lastLength -and $item.LastWriteTimeUtc -eq $lastWrite) {
            $same++
        } else {
            $same = 0
            $lastLength = $item.Length
            $lastWrite = $item.LastWriteTimeUtc
        }
    }
}

function Get-MediaDurationSeconds {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$FfprobePath
    )

    $raw = & $FfprobePath -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 $Path
    $duration = 0.0
    if (-not [double]::TryParse($raw, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$duration)) {
        throw "Impossibile leggere la durata del file: $Path"
    }

    return $duration
}

function Save-CompressedArchiveAudio {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourcePath,

        [Parameter(Mandatory = $true)]
        [string]$DestinationPath,

        [Parameter(Mandatory = $true)]
        [string]$FfmpegPath,

        [Parameter(Mandatory = $true)]
        [string]$FfprobePath,

        [Parameter(Mandatory = $true)]
        [decimal]$MaxMB
    )

    $maxBytes = [int64]($MaxMB * 1000 * 1000)
    $sourceItem = Get-Item -LiteralPath $SourcePath

    if ($sourceItem.Length -le $maxBytes) {
        Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath -Force
        return
    }

    $duration = Get-MediaDurationSeconds -Path $SourcePath -FfprobePath $FfprobePath
    $targetKbps = [Math]::Floor((($maxBytes * 8) / $duration / 1000) * 0.92)
    $bitrates = @(
        [Math]::Min(128, [Math]::Max(32, [int]$targetKbps)),
        64,
        48,
        32,
        24
    ) | Select-Object -Unique

    foreach ($bitrate in $bitrates) {
        $tmpPath = Join-Path (Split-Path -Parent $DestinationPath) ('audio_compresso_tmp_' + $bitrate + 'k.m4a')
        & $FfmpegPath -hide_banner -y -i $SourcePath -vn -ac 1 -ar 16000 -c:a aac -b:a "${bitrate}k" $tmpPath
        if ($LASTEXITCODE -ne 0) {
            if (Test-Path -LiteralPath $tmpPath) {
                Remove-Item -LiteralPath $tmpPath -Force
            }
            continue
        }

        $tmpItem = Get-Item -LiteralPath $tmpPath
        if ($tmpItem.Length -le $maxBytes) {
            Move-Item -LiteralPath $tmpPath -Destination $DestinationPath -Force
            return
        }

        Remove-Item -LiteralPath $tmpPath -Force
    }

    throw "Impossibile creare audio compresso sotto $MaxMB MB."
}

$resolved = Resolve-Path -LiteralPath $InputPath -ErrorAction SilentlyContinue
if (-not $resolved) {
    throw "File non trovato: $InputPath"
}

$InputPath = $resolved.Path
Wait-FileStable -Path $InputPath

$inputItem = Get-Item -LiteralPath $InputPath
$audioExtensions = @('.m4a', '.mp3', '.wav', '.aac', '.flac', '.ogg', '.webm', '.wma')
$videoExtensions = @('.mp4', '.mkv', '.mov', '.avi', '.webm')
$ext = $inputItem.Extension.ToLowerInvariant()

if ($audioExtensions -notcontains $ext -and $videoExtensions -notcontains $ext) {
    throw "Estensione non supportata: $ext"
}

$callName = '{0:yyyy-MM-dd HH.mm} - {1}' -f $inputItem.LastWriteTime, (Get-SafeName -Name $inputItem.BaseName)
$callDir = Join-Path (Join-Path $RootPath 'completate') $callName
New-Item -ItemType Directory -Force -Path $callDir | Out-Null

$ffmpeg = Resolve-Tool -Name 'ffmpeg'
$ffprobe = Resolve-Tool -Name 'ffprobe'
$audioPath = Join-Path $callDir 'audio.m4a'
$archiveAudioPath = Join-Path $callDir 'audio_compresso.m4a'
$isVideo = $videoExtensions -contains $ext

if ($isVideo) {
    & $ffmpeg -hide_banner -y -i $InputPath -vn -ac 1 -ar 16000 -c:a aac -b:a 128k $audioPath
    if ($LASTEXITCODE -ne 0) {
        throw 'Estrazione audio dal video fallita.'
    }
} else {
    $originalPath = Join-Path $callDir ('audio_originale' + $inputItem.Extension.ToLowerInvariant())
    Copy-Item -LiteralPath $InputPath -Destination $originalPath -Force

    if ($ext -eq '.m4a') {
        Copy-Item -LiteralPath $InputPath -Destination $audioPath -Force
    } else {
        & $ffmpeg -hide_banner -y -i $InputPath -vn -ac 1 -ar 16000 -c:a aac -b:a 128k $audioPath
        if ($LASTEXITCODE -ne 0) {
            throw 'Conversione audio in m4a fallita.'
        }
    }
}

$transcriptPath = Join-Path $callDir 'trascrizione.txt'
$summaryPath = Join-Path $callDir 'riassunto.md'

& (Join-Path $PSScriptRoot 'transcribe_with_groq.ps1') -AudioPath $audioPath -OutputPath $transcriptPath | Out-Host
& (Join-Path $PSScriptRoot 'summarize_with_claude.ps1') -TranscriptPath $transcriptPath -OutputPath $summaryPath | Out-Host

$contextTitle = Get-SummaryTitle -SummaryPath $summaryPath
if (-not $contextTitle) {
    $contextTitle = Get-ShortTitle -Title $inputItem.BaseName
}

Set-SummaryTitle -SummaryPath $summaryPath -Title $contextTitle

$finalCallName = '{0:yyyy-MM-dd HH.mm} - {1}' -f $inputItem.LastWriteTime, $contextTitle
$taskDir = Get-CallTaskDirectory -RootPath $RootPath -SummaryPath $summaryPath -Title $contextTitle
$finalCallDir = Get-UniqueDirectoryPath -Path (Join-Path $taskDir $finalCallName)
if ($finalCallDir -ne $callDir) {
    Move-Item -LiteralPath $callDir -Destination $finalCallDir
    $callDir = $finalCallDir
    $audioPath = Join-Path $callDir 'audio.m4a'
    $archiveAudioPath = Join-Path $callDir 'audio_compresso.m4a'
    $summaryPath = Join-Path $callDir 'riassunto.md'
}

Save-CompressedArchiveAudio -SourcePath $audioPath -DestinationPath $archiveAudioPath -FfmpegPath $ffmpeg -FfprobePath $ffprobe -MaxMB $ArchiveMaxMB

Get-ChildItem -LiteralPath $callDir -Force |
    Where-Object {
        $_.Name -ne 'audio_compresso.m4a' -and
        $_.Name -ne 'riassunto.md'
    } |
    Remove-Item -Recurse -Force

if ($isVideo -and -not $KeepVideo) {
    Remove-Item -LiteralPath $InputPath -Force
} elseif (-not $isVideo) {
    Remove-Item -LiteralPath $InputPath -Force
}

[pscustomobject]@{
    CallDirectory = $callDir
    TaskDirectory = $taskDir
    Audio = $archiveAudioPath
    Summary = $summaryPath
}
