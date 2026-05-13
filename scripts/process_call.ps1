param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,

    [Parameter(Mandatory = $false)]
    [string]$RootPath = '',

    [Parameter(Mandatory = $false)]
    [switch]$KeepVideo,

    [Parameter(Mandatory = $false)]
    [decimal]$ArchiveMaxMB = 19,

    [Parameter(Mandatory = $false)]
    [Alias('p')]
    [ValidateSet('gemini', 'claude', 'codex')]
    [string]$Provider = 'gemini',

    [Parameter(Mandatory = $false)]
    [string]$SummaryModel = '',

    [Parameter(Mandatory = $false)]
    [string]$TaskModel = ''
)

$ErrorActionPreference = 'Stop'
$Utf8NoBom = New-Object Text.UTF8Encoding($false)

if ([string]::IsNullOrWhiteSpace($RootPath)) {
    $scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $RootPath = Split-Path -Parent $scriptDir
}

$RootPath = (Resolve-Path -LiteralPath $RootPath).Path
$llmCommonPath = Join-Path $PSScriptRoot 'llm\common.ps1'
. $llmCommonPath

$llmProviderPath = Get-LlmProviderPath -ScriptRoot $PSScriptRoot -Provider $Provider
. $llmProviderPath

if ($Provider -eq 'codex') {
    throw 'Provider Codex non ancora implementato: definire prima CLI/API e modalita'' headless.'
}

if (-not (Test-LlmProviderAvailable)) {
    throw "Provider LLM non disponibile: $Provider"
}

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

function Split-MarkdownFrontmatter {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Text
    )

    $lines = @($Text -split '\r?\n')
    $frontmatter = @()
    $body = $lines
    $firstContentIndex = $null

    for ($i = 0; $i -lt $lines.Count; $i++) {
        if (-not [string]::IsNullOrWhiteSpace($lines[$i])) {
            $firstContentIndex = $i
            break
        }
    }

    if ($null -ne $firstContentIndex -and $lines[$firstContentIndex].Trim() -eq '---') {
        for ($i = $firstContentIndex + 1; $i -lt $lines.Count; $i++) {
            if ($lines[$i].Trim() -eq '---') {
                $frontmatter = @($lines[$firstContentIndex..$i])
                $bodyStart = $i + 1
                $body = if ($bodyStart -lt $lines.Count) { @($lines[$bodyStart..($lines.Count - 1)]) } else { @() }
                break
            }
        }
    }

    [pscustomobject]@{
        Frontmatter = $frontmatter
        Body = $body
    }
}

function Get-SummaryTitle {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SummaryPath
    )

    $content = [IO.File]::ReadAllText($SummaryPath, [Text.Encoding]::UTF8)
    $parts = Split-MarkdownFrontmatter -Text $content
    $lines = @($parts.Body)
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
    $parts = Split-MarkdownFrontmatter -Text $content
    $body = @($parts.Body)

    while ($body.Count -gt 0 -and [string]::IsNullOrWhiteSpace($body[0])) {
        $body = if ($body.Count -gt 1) { @($body[1..($body.Count - 1)]) } else { @() }
    }

    if ($body.Count -gt 0 -and $body[0] -match '^#\s+') {
        $body = if ($body.Count -gt 1) { @($body[1..($body.Count - 1)]) } else { @() }
    }

    while ($body.Count -gt 0 -and [string]::IsNullOrWhiteSpace($body[0])) {
        $body = if ($body.Count -gt 1) { @($body[1..($body.Count - 1)]) } else { @() }
    }

    if ($body.Count -gt 0 -and $body[0] -match '^##\s+') {
        $body = if ($body.Count -gt 1) { @($body[1..($body.Count - 1)]) } else { @() }
    }

    $lines = @()
    if ($parts.Frontmatter.Count -gt 0) {
        $lines += $parts.Frontmatter
        $lines += ''
    }
    $lines += @('# riassunto', "## $Title", '')
    $lines += $body

    [IO.File]::WriteAllText($SummaryPath, (($lines -join [Environment]::NewLine).Trim() + [Environment]::NewLine), $Utf8NoBom)
}

function Get-TaskNameFromDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RootPath,

        [Parameter(Mandatory = $true)]
        [string]$TaskDirectory
    )

    $taskRoot = Join-Path (Join-Path $RootPath 'completate') 'Task'
    $resolvedTaskRoot = Resolve-Path -LiteralPath $taskRoot -ErrorAction SilentlyContinue
    $resolvedTaskDirectory = Resolve-Path -LiteralPath $TaskDirectory -ErrorAction SilentlyContinue

    if (-not $resolvedTaskRoot -or -not $resolvedTaskDirectory) {
        return ''
    }

    $taskRootPath = $resolvedTaskRoot.Path.TrimEnd('\')
    $taskDirectoryPath = $resolvedTaskDirectory.Path.TrimEnd('\')
    $parent = (Split-Path -Parent $taskDirectoryPath).TrimEnd('\')

    if ([string]::Equals($parent, $taskRootPath, [StringComparison]::OrdinalIgnoreCase)) {
        return Split-Path -Leaf $taskDirectoryPath
    }

    return ''
}

function Set-SummaryFrontmatter {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SummaryPath,

        [Parameter(Mandatory = $true)]
        [datetime]$Timestamp,

        [Parameter(Mandatory = $false)]
        [string]$TaskName = ''
    )

    $content = [IO.File]::ReadAllText($SummaryPath, [Text.Encoding]::UTF8).Trim()
    $parts = Split-MarkdownFrontmatter -Text $content
    $metadataLines = @()
    $hasTags = $false

    if ($parts.Frontmatter.Count -gt 0) {
        $metadataEnd = $parts.Frontmatter.Count - 2
        for ($i = 1; $i -le $metadataEnd; $i++) {
            $line = $parts.Frontmatter[$i]
            if ($line -match '^\s*(data|ora|task)\s*:') {
                continue
            }

            if ($line -match '^\s*tags\s*:') {
                $hasTags = $true
            }

            $metadataLines += $line
        }
    }

    if (-not $hasTags) {
        $metadataLines += 'tags: [call]'
    }

    $frontmatter = @(
        '---',
        ('data: {0:yyyy-MM-dd}' -f $Timestamp),
        ('ora: "{0:HH:mm}"' -f $Timestamp)
    )

    if (-not [string]::IsNullOrWhiteSpace($TaskName)) {
        $frontmatter += ('task: "[[{0}]]"' -f $TaskName)
    }

    $frontmatter += $metadataLines
    $frontmatter += '---'

    $body = @($parts.Body)
    while ($body.Count -gt 0 -and [string]::IsNullOrWhiteSpace($body[0])) {
        $body = if ($body.Count -gt 1) { @($body[1..($body.Count - 1)]) } else { @() }
    }

    $lines = $frontmatter + @('') + $body
    [IO.File]::WriteAllText($SummaryPath, (($lines -join [Environment]::NewLine).Trim() + [Environment]::NewLine), $Utf8NoBom)
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
        [string]$Title,

        [Parameter(Mandatory = $false)]
        [string]$Model = ''
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

    if (-not (Test-LlmProviderAvailable)) {
        return $taskRoot
    }

    if ([string]::IsNullOrWhiteSpace($Model)) {
        $Model = Get-DefaultTaskModel
    }

    try {
        $prompt = New-TaskClassificationPrompt -Tasks $tasks -SummaryPath $SummaryPath -Title $Title
        $answer = Invoke-TaskClassification -Prompt $prompt -Model $Model
        $selected = Select-TaskDirectoryFromAnswer -Tasks $tasks -Answer $answer
    } catch {
        return $taskRoot
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
$summaryScript = Join-Path $PSScriptRoot "summarize_with_$Provider.ps1"
$summaryArgs = @{
    TranscriptPath = $transcriptPath
    OutputPath = $summaryPath
}
if (-not [string]::IsNullOrWhiteSpace($SummaryModel)) {
    $summaryArgs.Model = $SummaryModel
}
& $summaryScript @summaryArgs | Out-Host

$contextTitle = Get-SummaryTitle -SummaryPath $summaryPath
if (-not $contextTitle) {
    $contextTitle = Get-ShortTitle -Title $inputItem.BaseName
}

Set-SummaryTitle -SummaryPath $summaryPath -Title $contextTitle

$finalCallName = '{0:yyyy-MM-dd HH.mm} - {1}' -f $inputItem.LastWriteTime, $contextTitle
$taskDir = Get-CallTaskDirectory -RootPath $RootPath -SummaryPath $summaryPath -Title $contextTitle -Model $TaskModel
$taskName = Get-TaskNameFromDirectory -RootPath $RootPath -TaskDirectory $taskDir
$finalCallDir = Get-UniqueDirectoryPath -Path (Join-Path $taskDir $finalCallName)
if ($finalCallDir -ne $callDir) {
    Move-Item -LiteralPath $callDir -Destination $finalCallDir
    $callDir = $finalCallDir
    $audioPath = Join-Path $callDir 'audio.m4a'
    $archiveAudioPath = Join-Path $callDir 'audio_compresso.m4a'
    $summaryPath = Join-Path $callDir 'riassunto.md'
}

Set-SummaryFrontmatter -SummaryPath $summaryPath -Timestamp $inputItem.LastWriteTime -TaskName $taskName

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

$rebuildIndexesPath = Join-Path $PSScriptRoot 'rebuild_indexes.ps1'
if (Test-Path -LiteralPath $rebuildIndexesPath) {
    & $rebuildIndexesPath -RootPath $RootPath | Out-Host
}

[pscustomobject]@{
    CallDirectory = $callDir
    TaskDirectory = $taskDir
    Audio = $archiveAudioPath
    Summary = $summaryPath
    Provider = $Provider
}
