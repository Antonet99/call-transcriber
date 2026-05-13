param(
    [Parameter(Mandatory = $true)]
    [string]$AudioPath,

    [Parameter(Mandatory = $false)]
    [string]$OutputPath,

    [Parameter(Mandatory = $false)]
    [decimal]$MaxMB = 19,

    [Parameter(Mandatory = $false)]
    [decimal]$ChunkTargetMB = 18,

    [Parameter(Mandatory = $false)]
    [string]$Model = 'whisper-large-v3-turbo'
)

$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8
$Utf8NoBom = New-Object Text.UTF8Encoding($false)

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

function Invoke-GroqTranscription {
    param(
        [Parameter(Mandatory = $true)]
        [string]$InputPath,

        [Parameter(Mandatory = $true)]
        [string]$TextPath,

        [Parameter(Mandatory = $true)]
        [string]$GroqModel
    )

    $apiKey = [Environment]::GetEnvironmentVariable('GROQ_API_KEY', 'Process')
    if (-not $apiKey) {
        $apiKey = [Environment]::GetEnvironmentVariable('GROQ_API_KEY', 'User')
    }
    if (-not $apiKey) {
        $apiKey = [Environment]::GetEnvironmentVariable('GROQ_API_KEY', 'Machine')
    }
    if (-not $apiKey) {
        throw 'Variabile ambiente GROQ_API_KEY non impostata.'
    }

    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if (-not $curl) {
        throw 'curl.exe non trovato.'
    }

    & $curl.Source 'https://api.groq.com/openai/v1/audio/transcriptions' `
        -sS `
        --fail-with-body `
        -H "Authorization: Bearer $apiKey" `
        -H 'Content-Type: multipart/form-data' `
        -F "file=@$InputPath" `
        -F "model=$GroqModel" `
        -F 'response_format=text' `
        -F 'temperature=0' `
        -o $TextPath

    if ($LASTEXITCODE -ne 0) {
        throw "Chiamata Groq fallita per $InputPath"
    }

    $content = if (Test-Path -LiteralPath $TextPath) {
        [IO.File]::ReadAllText($TextPath, [Text.Encoding]::UTF8)
    } else {
        ''
    }
    if (-not $content -or -not $content.Trim()) {
        throw "Trascrizione vuota per $InputPath"
    }
}

$resolved = Resolve-Path -LiteralPath $AudioPath -ErrorAction SilentlyContinue
if (-not $resolved) {
    throw "Audio non trovato: $AudioPath"
}

$AudioPath = $resolved.Path
if (-not $OutputPath) {
    $OutputPath = Join-Path (Split-Path -Parent $AudioPath) 'trascrizione.txt'
}

$ffmpeg = Resolve-Tool -Name 'ffmpeg'
$ffprobe = Resolve-Tool -Name 'ffprobe'
$workDir = Join-Path (Split-Path -Parent $OutputPath) '_chunks'
if (Test-Path -LiteralPath $workDir) {
    Remove-Item -LiteralPath $workDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $workDir | Out-Null

$maxBytes = [int64]($MaxMB * 1000 * 1000)
$targetBytes = [int64]($ChunkTargetMB * 1000 * 1000)
$audioItem = Get-Item -LiteralPath $AudioPath
$parts = @()

if ($audioItem.Length -le $maxBytes) {
    $partText = Join-Path $workDir 'chunk_001.txt'
    Invoke-GroqTranscription -InputPath $AudioPath -TextPath $partText -GroqModel $Model
    $parts += $partText
} else {
    $duration = Get-MediaDurationSeconds -Path $AudioPath -FfprobePath $ffprobe
    $chunkCount = [Math]::Max(2, [Math]::Ceiling($audioItem.Length / $targetBytes))
    $chunkSeconds = [Math]::Max(60, [Math]::Ceiling($duration / $chunkCount))

    & $ffmpeg -hide_banner -y -i $AudioPath -vn -ac 1 -ar 16000 -c:a aac -b:a 96k -f segment -segment_time $chunkSeconds -reset_timestamps 1 (Join-Path $workDir 'chunk_%03d.m4a')
    if ($LASTEXITCODE -ne 0) {
        throw 'Creazione chunk audio fallita.'
    }

    $chunks = Get-ChildItem -LiteralPath $workDir -Filter 'chunk_*.m4a' | Sort-Object Name
    if (-not $chunks) {
        throw 'Nessun chunk audio generato.'
    }

    foreach ($chunk in $chunks) {
        if ($chunk.Length -gt $maxBytes) {
            $compressed = Join-Path $workDir ($chunk.BaseName + '_small.m4a')
            & $ffmpeg -hide_banner -y -i $chunk.FullName -vn -ac 1 -ar 16000 -c:a aac -b:a 64k $compressed
            if ($LASTEXITCODE -ne 0) {
                throw "Compressione chunk fallita: $($chunk.Name)"
            }

            $smallItem = Get-Item -LiteralPath $compressed
            if ($smallItem.Length -gt $maxBytes) {
                throw "Chunk ancora sopra $MaxMB MB dopo compressione: $($chunk.Name)"
            }

            $inputChunk = $compressed
        } else {
            $inputChunk = $chunk.FullName
        }

        $partText = Join-Path $workDir ([IO.Path]::GetFileNameWithoutExtension($inputChunk) + '.txt')
        Invoke-GroqTranscription -InputPath $inputChunk -TextPath $partText -GroqModel $Model
        $parts += $partText
    }
}

$combined = New-Object System.Collections.Generic.List[string]
for ($i = 0; $i -lt $parts.Count; $i++) {
    $text = [IO.File]::ReadAllText($parts[$i], [Text.Encoding]::UTF8)
    if ($parts.Count -gt 1) {
        $combined.Add("[PARTE $($i + 1)]")
        $combined.Add('')
    }
    $combined.Add($text.Trim())
    $combined.Add('')
}

[IO.File]::WriteAllText($OutputPath, (($combined -join [Environment]::NewLine).Trim()), $Utf8NoBom)

[pscustomobject]@{
    OutputPath = $OutputPath
    ChunkCount = $parts.Count
    Model = $Model
}
