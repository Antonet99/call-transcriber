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
    [string]$TaskModel = '',

    [Parameter(Mandatory = $false)]
    [ValidateRange(1, 5)]
    [int]$GeminiCapacityAttempts = 2
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

function Set-ActiveLlmProvider {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('gemini', 'claude', 'codex')]
        [string]$Name
    )

    $llmProviderPath = Get-LlmProviderPath -ScriptRoot $PSScriptRoot -Provider $Name
    . $llmProviderPath

    if ($Name -eq 'codex') {
        throw 'Provider Codex non ancora implementato: definire prima CLI/API e modalita'' headless.'
    }

    if (-not (Test-LlmProviderAvailable)) {
        throw "Provider LLM non disponibile: $Name"
    }

    $script:ActiveProvider = $Name
}

Set-ActiveLlmProvider -Name $Provider

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

function Convert-ToKebabTag {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    $clean = ($Value.ToLowerInvariant() -replace '[^a-z0-9]+', '-' -replace '^-+|-+$', '').Trim()
    return $clean
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

function Get-SummaryPeople {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SummaryPath
    )

    $content = [IO.File]::ReadAllText($SummaryPath, [Text.Encoding]::UTF8)
    $parts = Split-MarkdownFrontmatter -Text $content
    if ($parts.Frontmatter.Count -eq 0) {
        return @()
    }

    for ($i = 1; $i -lt ($parts.Frontmatter.Count - 1); $i++) {
        $line = $parts.Frontmatter[$i]
        if ($line -match '^\s*persone\s*:\s*(.*?)\s*$') {
            if (-not [string]::IsNullOrWhiteSpace($Matches[1])) {
                return @(Convert-FrontmatterArray -Value $Matches[1])
            }

            $people = @()
            for ($j = $i + 1; $j -lt ($parts.Frontmatter.Count - 1); $j++) {
                if ($parts.Frontmatter[$j] -match '^\s*-\s*(.+?)\s*$') {
                    $people += $Matches[1].Trim()
                    continue
                }
                break
            }

            return @($people)
        }
    }

    return @()
}

function Add-PeopleToTitle {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Title,

        [Parameter(Mandatory = $false)]
        [string[]]$People = @()
    )

    $cleanPeople = @($People | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique)
    if ($cleanPeople.Count -eq 0) {
        return $Title
    }

    $missing = @($cleanPeople | Where-Object {
        $pattern = '(?i)(^|[\s,;-])' + [regex]::Escape($_) + '($|[\s,;-])'
        $Title -notmatch $pattern
    })

    if ($missing.Count -eq 0) {
        return $Title
    }

    $prefix = if ($missing.Count -eq 1) {
        $missing[0]
    } elseif ($missing.Count -eq 2) {
        "$($missing[0]) e $($missing[1])"
    } else {
        (($missing | Select-Object -First ($missing.Count - 1)) -join ', ') + ' e ' + $missing[-1]
    }

    return ($prefix + ', ' + $Title)
}

function Get-CallTitleFromDirectoryName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DirectoryName
    )

    if ($DirectoryName -match '^\d{4}-\d{2}-\d{2}\s+\d{2}\.\d{2}\s+-\s+(.+)$') {
        return $Matches[1].Trim()
    }

    return $DirectoryName.Trim()
}

function Get-SummaryFileName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Title
    )

    return (Get-SafeName -Name $Title) + '.md'
}

function Rename-SummaryFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SummaryPath,

        [Parameter(Mandatory = $true)]
        [string]$Title
    )

    $targetName = Get-SummaryFileName -Title $Title
    $targetPath = Join-Path (Split-Path -Parent $SummaryPath) $targetName
    if ([string]::Equals($SummaryPath, $targetPath, [StringComparison]::OrdinalIgnoreCase)) {
        return $SummaryPath
    }

    Move-Item -LiteralPath $SummaryPath -Destination $targetPath -Force
    return $targetPath
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

function Test-GeminiCapacityError {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    return ($Message -match '(?i)MODEL_CAPACITY_EXHAUSTED|RESOURCE_EXHAUSTED|No capacity available for model|RetryableQuotaError')
}

function Invoke-SummaryForProvider {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('gemini', 'claude', 'codex')]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [string]$TranscriptPath,

        [Parameter(Mandatory = $true)]
        [string]$OutputPath,

        [Parameter(Mandatory = $false)]
        [string]$Model = '',

        [Parameter(Mandatory = $false)]
        [ValidateSet('', 'low', 'medium', 'high', 'xhigh', 'max')]
        [string]$Effort = ''
    )

    $summaryScript = Join-Path $PSScriptRoot "summarize_with_$Name.ps1"
    $summaryArgs = @{
        TranscriptPath = $TranscriptPath
        OutputPath = $OutputPath
    }

    if (-not [string]::IsNullOrWhiteSpace($Model)) {
        $summaryArgs.Model = $Model
    }

    if ($Name -eq 'claude' -and -not [string]::IsNullOrWhiteSpace($Effort)) {
        $summaryArgs.Effort = $Effort
    }

    & $summaryScript @summaryArgs | Out-Host
}

function Invoke-SummaryWithFallback {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TranscriptPath,

        [Parameter(Mandatory = $true)]
        [string]$OutputPath,

        [Parameter(Mandatory = $true)]
        [ValidateSet('gemini', 'claude', 'codex')]
        [string]$Name,

        [Parameter(Mandatory = $false)]
        [string]$Model = '',

        [Parameter(Mandatory = $false)]
        [int]$GeminiAttempts = 2
    )

    if ($Name -ne 'gemini') {
        Invoke-SummaryForProvider -Name $Name -TranscriptPath $TranscriptPath -OutputPath $OutputPath -Model $Model
        return $Name
    }

    $lastCapacityError = $null
    for ($attempt = 1; $attempt -le $GeminiAttempts; $attempt++) {
        try {
            Invoke-SummaryForProvider -Name 'gemini' -TranscriptPath $TranscriptPath -OutputPath $OutputPath -Model $Model
            return 'gemini'
        } catch {
            $message = $_.Exception.Message
            if (-not (Test-GeminiCapacityError -Message $message)) {
                throw
            }

            $lastCapacityError = $_
            Write-Warning ("Gemini non ha capacita' disponibile per il modello richiesto. Tentativo {0}/{1} fallito." -f $attempt, $GeminiAttempts)
        }
    }

    Write-Warning 'Fallback su Claude: uso claude-sonnet-4-6 con effort medium.'
    Set-ActiveLlmProvider -Name 'claude'
    Invoke-SummaryForProvider `
        -Name 'claude' `
        -TranscriptPath $TranscriptPath `
        -OutputPath $OutputPath `
        -Model 'claude-sonnet-4-6' `
        -Effort 'medium'

    if (-not (Test-Path -LiteralPath $OutputPath)) {
        throw $lastCapacityError
    }

    return 'claude'
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
$summaryProvider = Invoke-SummaryWithFallback `
    -TranscriptPath $transcriptPath `
    -OutputPath $summaryPath `
    -Name $Provider `
    -Model $SummaryModel `
    -GeminiAttempts $GeminiCapacityAttempts

$contextTitle = Get-SummaryTitle -SummaryPath $summaryPath
if (-not $contextTitle) {
    $contextTitle = Get-ShortTitle -Title $inputItem.BaseName
}
$contextTitle = Add-PeopleToTitle -Title $contextTitle -People (Get-SummaryPeople -SummaryPath $summaryPath)

Set-SummaryTitle -SummaryPath $summaryPath -Title $contextTitle

$finalCallName = '{0:yyyy-MM-dd HH.mm} - {1}' -f $inputItem.LastWriteTime, $contextTitle
$activeTaskModel = if ($summaryProvider -eq $Provider) { $TaskModel } else { '' }
$taskDir = Get-CallTaskDirectory -RootPath $RootPath -SummaryPath $summaryPath -Title $contextTitle -Model $activeTaskModel
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
$summaryPath = Rename-SummaryFile -SummaryPath $summaryPath -Title (Get-CallTitleFromDirectoryName -DirectoryName (Split-Path -Leaf $callDir))

Save-CompressedArchiveAudio -SourcePath $audioPath -DestinationPath $archiveAudioPath -FfmpegPath $ffmpeg -FfprobePath $ffprobe -MaxMB $ArchiveMaxMB

Get-ChildItem -LiteralPath $callDir -Force |
    Where-Object {
        $_.Name -ne 'audio_compresso.m4a' -and
        $_.Name -ne (Split-Path -Leaf $summaryPath)
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
    Provider = $script:ActiveProvider
}
