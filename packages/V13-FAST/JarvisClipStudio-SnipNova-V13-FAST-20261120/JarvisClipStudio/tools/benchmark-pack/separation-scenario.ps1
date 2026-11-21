<#
.SYNOPSIS
  SnipNova benchmark pack - music-heavy separation check for the RTX 4050.

.DESCRIPTION
  The user-machine leg of separation Stage 3: proves, on the real
  machine and the real models, the two things the sandbox could only
  prove on synthetic best cases -

    1. HALLUCINATION PROBE: on a music-heavy source with a labelled
       MUSIC-ONLY window (no speech at all in that stretch), the
       separation-OFF transcript must not be trusted and the
       separation-ON transcript should contain ZERO words inside the
       window. Phantom words in music-only stretches are the failure
       this feature exists to remove.
    2. EXPORT ATTENUATION: in the same window, an exported clip from
       the ON task (render strip active) should measure far quieter
       than the same-range clip from the OFF task - the source's own
       music is gone from under the voice chain.

  Design: two tasks on the SAME content under DIFFERENT filenames.
  The app's transcript/analysis caches key on the source reference,
  so reusing one upload for both legs would let the second leg reuse
  the first leg's transcript and quietly invalidate the comparison.
  Two renamed copies (fixture-off.mp4 / fixture-on.mp4) make each leg
  a first-time source. The script verifies each leg from the database
  afterwards (tasks.separation_opt_in and tasks.cache_hit): a leg
  with the wrong opt-in recorded, or cache_hit = true, is reported
  INVALID - the numbers are printed but must not be read as a result.

  Fixture: any music-heavy video with at least one stretch of pure
  music (an instrumental intro/interlude, a concert clip, a music
  video section without vocals). You must know ONE music-only window
  in SOURCE seconds: pass -MusicWindowStart / -MusicWindowEnd. Pick a
  window of 10-60 s with no speech, no lyrics, no speech-like vocals.

  Setup (BEFORE running, in the app root):
    - .env contains SEPARATION_ENABLED=true, then restart the stack
      (docker compose up -d) so backend AND worker run with the flag.
      BOTH legs use this same stack; leg A simply does not opt in.
    - Two renamed copies of the fixture file ready to upload.
    - Nothing else generating while the scenario runs.

  Then, from this folder:
    .\separation-scenario.ps1 -MusicWindowStart 30 -MusicWindowEnd 55

  The script samples resources, walks you through the two tasks,
  harvests the database + worker log, measures both exports with
  ffmpeg volumedetect inside the worker container, counts transcript
  words inside the music window from each task's own transcript
  cache, and writes benchmark-runs\separation-<timestamp>\report.md.
  Anything not measured is reported NOT OBSERVED - never estimated.
#>
[CmdletBinding()]
param(
    [string]$ProjectDir = "",
    [int]$SampleSeconds = 2,
    [int]$MaxWaitMinutes = 120,
    [double]$MusicWindowStart = -1,
    [double]$MusicWindowEnd = -1
)

$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'benchmark-lib.ps1')

if (-not $ProjectDir) { $ProjectDir = (Resolve-Path (Join-Path $here '..\..')).Path }
$appRoot = $ProjectDir
if (-not (Test-Path (Join-Path $appRoot 'docker-compose.yml'))) {
    throw "No docker-compose.yml in $appRoot - point -ProjectDir at the app root."
}
if ($MusicWindowStart -lt 0 -or $MusicWindowEnd -le $MusicWindowStart) {
    throw "Pass a music-only window in source seconds, e.g. -MusicWindowStart 30 -MusicWindowEnd 55 (end after start)."
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$runDir = Join-Path $ProjectDir (Join-Path 'benchmark-runs' "separation-$stamp")
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$csvPath = Join-Path $runDir 'resources.csv'
$stopFile = Join-Path $runDir 'sampler.stop'

function Invoke-Psql([string]$Sql) {
    $out = docker compose exec -T postgres psql -U supoclip -d supoclip -At -F "|" -c $Sql
    if ($LASTEXITCODE -ne 0) { throw "psql failed for: $Sql" }
    return ($out | Out-String).Trim()
}

function Get-SepTaskRow([string]$Id) {
    $row = Invoke-Psql "SELECT status, COALESCE(separation_opt_in::text,''), COALESCE(cache_hit::text,''), COALESCE(stage_timings_json,''), COALESCE(started_at::text,''), COALESCE(completed_at::text,'') FROM tasks WHERE id = '$Id';"
    if (-not $row) { return $null }
    $p = $row -split '\|', 6
    return [pscustomobject]@{
        Status = $p[0]; OptIn = $p[1]; CacheHit = $p[2]
        TimingsJson = $p[3]; StartedAt = $p[4]; CompletedAt = $p[5]
    }
}

function Wait-NewTask([string]$Marker, [string]$LegLabel) {
    $deadline = (Get-Date).AddMinutes(20)
    while ((Get-Date) -lt $deadline) {
        $cand = Invoke-Psql "SELECT id FROM tasks WHERE created_at > '$Marker'::timestamp ORDER BY created_at ASC LIMIT 1;"
        if ($cand) { return $cand.Trim() }
        Start-Sleep -Seconds 10
    }
    throw "No new task appeared for the $LegLabel leg within 20 min. Aborting; nothing about this leg was measured."
}

function Wait-TaskDone([string]$Id, [string]$LegLabel) {
    $deadline = (Get-Date).AddMinutes($MaxWaitMinutes)
    while ((Get-Date) -lt $deadline) {
        $row = Get-SepTaskRow $Id
        if (-not $row) { throw "Task $Id disappeared from the database." }
        if ($row.Status -in @('completed', 'failed', 'error', 'cancelled')) { return $row }
        Start-Sleep -Seconds 10
    }
    throw "Task $Id still '$($row.Status)' after $MaxWaitMinutes min ($LegLabel leg). Aborting before the next leg so the legs never overlap."
}

function Format-MeanDb($Value) {
    if ($null -eq $Value) { return 'NOT OBSERVED' }
    if ([double]::IsNegativeInfinity([double]$Value)) { return '-inf dB (digital silence)' }
    return ('{0:N1} dB' -f [double]$Value)
}

Write-Host ""
Write-Host "Separation scenario - music-heavy hallucination + attenuation check" -ForegroundColor Cyan
Write-Host "Run folder: $runDir"
Write-Host "Music-only window: $MusicWindowStart s to $MusicWindowEnd s (source time)"
Write-Host ""
Write-Host "Preconditions (see this script's header for detail):"
Write-Host "  - Stack restarted with SEPARATION_ENABLED=true in .env"
Write-Host "  - Two renamed copies of the SAME music-heavy file ready (e.g. fixture-off.mp4 / fixture-on.mp4)"
Write-Host "  - The window above is pure music: no speech, no lyrics"
Write-Host "  - Nothing else generating"
Write-Host ""
$go = Read-Host "Type GO when the stack is up that way"
if ($go -ne 'GO') { Write-Host "Aborted."; return }

Push-Location $appRoot
try {
    $marker0 = Invoke-Psql "SELECT COALESCE(MAX(created_at)::text,'1970-01-01 00:00:00') FROM tasks;"
} finally { Pop-Location }

$sampler = Start-Process powershell -PassThru -WindowStyle Hidden -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $here 'sampler.ps1'),
    '-OutCsv', $csvPath, '-StopFile', $stopFile, '-IntervalSeconds', "$SampleSeconds"
)

$offTaskId = $null; $onTaskId = $null
try {
    Write-Host ""
    Write-Host "STEP 1/2 - LEG A (separation OFF, the control):" -ForegroundColor Yellow
    Write-Host "  In the app: New generation -> upload the OFF copy of the fixture."
    Write-Host "  Settings: niche = Music (manual override), clips = 5, length = Medium,"
    Write-Host "  Music = Off, quality = Full Quality. Leave the voice/music separation"
    Write-Host "  opt-in UNticked. Start the generation, then press Enter here."
    [void](Read-Host "Press Enter once the OFF task has been started")
    Push-Location $appRoot
    try { $offTaskId = Wait-NewTask $marker0 'OFF' } finally { Pop-Location }
    Write-Host "Detected OFF task: $offTaskId - waiting for it to finish..."
    Push-Location $appRoot
    try { $offRow = Wait-TaskDone $offTaskId 'OFF' } finally { Pop-Location }
    Write-Host "OFF task finished with status: $($offRow.Status)"

    $marker1 = $null
    Push-Location $appRoot
    try { $marker1 = Invoke-Psql "SELECT COALESCE(MAX(created_at)::text,'1970-01-01 00:00:00') FROM tasks;" } finally { Pop-Location }

    Write-Host ""
    Write-Host "STEP 2/2 - LEG B (separation ON):" -ForegroundColor Yellow
    Write-Host "  In the app: New generation -> upload the ON copy (the renamed twin)."
    Write-Host "  SAME settings as leg A, but TICK the voice/music separation opt-in."
    Write-Host "  Start the generation, then press Enter here."
    [void](Read-Host "Press Enter once the ON task has been started")
    Push-Location $appRoot
    try { $onTaskId = Wait-NewTask $marker1 'ON' } finally { Pop-Location }
    Write-Host "Detected ON task: $onTaskId - waiting for it to finish..."
    Push-Location $appRoot
    try { $onRow = Wait-TaskDone $onTaskId 'ON' } finally { Pop-Location }
    Write-Host "ON task finished with status: $($onRow.Status)"
} finally {
    New-Item -ItemType File -Force -Path $stopFile | Out-Null
    if ($sampler -and -not $sampler.HasExited) { $sampler | Wait-Process -Timeout 30 -ErrorAction SilentlyContinue }
}

# ---------------------------------------------------------------- harvest
$logPath = Join-Path $runDir 'worker-log.txt'
$logLines = @()
$tempDir = $null
try {
    Push-Location $appRoot
    $logLines = @(docker compose logs --no-color worker 2>$null)
    $tempDir = (docker compose exec -T worker printenv TEMP_DIR 2>$null | Out-String).Trim()
    Pop-Location
} catch {
    Write-Host "Could not read worker state via docker compose: $_" -ForegroundColor Red
}
$logLines | Set-Content -Path $logPath -Encoding UTF8
if (-not $tempDir) { $tempDir = '/app/uploads' }

function Get-LegData([string]$TaskId) {
    $data = [pscustomobject]@{
        TaskId = $TaskId; Row = $null; SourceUrl = $null; SourceType = $null
        Clips = @(); TranscriptWords = $null; TranscriptWordList = @()
        AttenuationMeanDb = $null; AttenuationClip = $null; AttenuationOverlap = $null
    }
    if (-not $TaskId) { return $data }
    Push-Location $appRoot
    try {
        $data.Row = Get-SepTaskRow $TaskId
        $src = Invoke-Psql "SELECT s.url, s.type FROM sources s JOIN tasks t ON t.source_id = s.id WHERE t.id = '$TaskId' LIMIT 1;"
        if ($src) {
            $sp = $src -split '\|', 2
            $data.SourceUrl = $sp[0]; $data.SourceType = $sp[1]
        }
        $clipsRaw = Invoke-Psql "SELECT filename, file_path, start_time, end_time, duration FROM generated_clips WHERE task_id = '$TaskId' ORDER BY clip_order ASC;"
        if ($clipsRaw) {
            foreach ($line in ($clipsRaw -split "`n")) {
                if (-not $line.Trim()) { continue }
                $p = $line -split '\|', 5
                $data.Clips += [pscustomobject]@{
                    Filename = $p[0]; FilePath = $p[1]
                    StartSec = ConvertFrom-MmSs $p[2]; EndSec = ConvertFrom-MmSs $p[3]
                    Duration = [double]$p[4]
                }
            }
        }
        # Transcript words inside the music window, from this task's own
        # transcript cache (beside the source inside the app temp tree).
        $cachePath = $null
        if ($data.SourceUrl -like 'upload://*') {
            $name = $data.SourceUrl -replace '^upload://', ''
            if ($name -like 's3/*') { $name = Split-Path $name -Leaf }
            $stem = [System.IO.Path]::GetFileNameWithoutExtension($name)
            $cachePath = "$tempDir/uploads/$stem.transcript_cache.json"
        } elseif ($data.SourceUrl -match 'v=([A-Za-z0-9_-]{11})') {
            $cachePath = "$tempDir/$($Matches[1]).transcript_cache.json"
        }
        if ($cachePath) {
            try {
                $oldEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
                $jsonText = (docker compose exec -T worker cat $cachePath 2>$null | Out-String)
                $ErrorActionPreference = $oldEap
                if ($jsonText -and $jsonText.Trim().StartsWith('{')) {
                    $cache = $jsonText | ConvertFrom-Json
                    $words = @(Get-TranscriptWindowWords -Cache $cache -WindowStart $MusicWindowStart -WindowEnd $MusicWindowEnd)
                    $data.TranscriptWordList = $words
                    $data.TranscriptWords = $words.Count
                }
            } catch { $data.TranscriptWords = $null }
        }
        # Export attenuation: the clip overlapping the music window most,
        # measured in the overlap with ffmpeg volumedetect (worker ffmpeg).
        $best = $null; $bestOverlap = $null
        foreach ($clip in $data.Clips) {
            if ($null -eq $clip.StartSec -or $null -eq $clip.EndSec) { continue }
            $ov = Get-WindowOverlap -ClipStart $clip.StartSec -ClipEnd $clip.EndSec -WindowStart $MusicWindowStart -WindowEnd $MusicWindowEnd
            if ($ov -and (-not $bestOverlap -or $ov.Seconds -gt $bestOverlap.Seconds)) {
                $best = $clip; $bestOverlap = $ov
            }
        }
        if ($best) {
            $data.AttenuationClip = $best.Filename
            $data.AttenuationOverlap = $bestOverlap
            try {
                $oldEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
                $volOut = (docker compose exec -T worker ffmpeg -ss $bestOverlap.LocalStart -t $bestOverlap.Seconds -i "$($best.FilePath)" -vn -af volumedetect -f null - 2>&1 | Out-String)
                $ErrorActionPreference = $oldEap
                $data.AttenuationMeanDb = Get-VolumeDetectMean -Text $volOut
            } catch { $data.AttenuationMeanDb = $null }
        }
    } finally { Pop-Location }
    return $data
}

$off = Get-LegData $offTaskId
$on = Get-LegData $onTaskId

function Get-LegValidity($Leg, [bool]$ExpectOptIn) {
    $problems = @()
    if (-not $Leg.Row) { $problems += 'task row not found at harvest' }
    else {
        if ($Leg.Row.Status -ne 'completed') { $problems += "task status is '$($Leg.Row.Status)', not 'completed'" }
        $expected = if ($ExpectOptIn) { 'true' } else { 'false' }
        if ($Leg.Row.OptIn -ne $expected) { $problems += "separation_opt_in recorded as '$($Leg.Row.OptIn)', expected '$expected'" }
        if ($Leg.Row.CacheHit -eq 'true') { $problems += 'cache_hit = true (transcript/analysis were reused - not a fresh measurement)' }
    }
    return $problems
}
$offProblems = @(Get-LegValidity $off $false)
$onProblems = @(Get-LegValidity $on $true)

function Get-TranscriptionSeconds($Row) {
    if (-not $Row -or -not $Row.TimingsJson) { return $null }
    try {
        $t = $Row.TimingsJson | ConvertFrom-Json
        if ($t.PSObject.Properties.Name -contains 'transcription_seconds') { return [double]$t.transcription_seconds }
    } catch { }
    return $null
}
function Get-WallSeconds($Row) {
    if (-not $Row -or -not $Row.StartedAt -or -not $Row.CompletedAt) { return $null }
    try { return ([datetime]$Row.CompletedAt - [datetime]$Row.StartedAt).TotalSeconds } catch { return $null }
}

$sepRuns = @($logLines | Where-Object { $_ -match 'Separating vocals for' }).Count
$sepDone = @($logLines | Where-Object { $_ -match 'Separation complete for' }).Count
$sepStemUsed = @($logLines | Where-Object { $_ -match 'Transcribing the separated vocals stem' }).Count
$sepDeviceCuda = @($logLines | Where-Object { $_ -match 'Separating vocals.*device=cuda' }).Count
$sepDeviceCpu = @($logLines | Where-Object { $_ -match 'Separating vocals.*device=cpu' }).Count
$vramSummary = @(Get-VramStageSummary -Lines $logLines)
$sampleStats = Read-SampleStats -CsvPath $csvPath

$attenuationDb = $null
if ($null -ne $off.AttenuationMeanDb -and $null -ne $on.AttenuationMeanDb) {
    $attenuationDb = [double]$off.AttenuationMeanDb - [double]$on.AttenuationMeanDb
}

# ---------------------------------------------------------------- report
$lines = New-Object System.Collections.ArrayList
[void]$lines.Add("# Separation scenario - $stamp")
[void]$lines.Add("")
[void]$lines.Add("Music-heavy separation check: hallucination probe + export attenuation, OFF leg vs ON leg on the same content under two filenames.")
[void]$lines.Add("Music-only window: $MusicWindowStart s to $MusicWindowEnd s (source time). Numbers are this machine's; anything unmeasured says NOT OBSERVED.")
[void]$lines.Add("")
[void]$lines.Add("## Legs")
[void]$lines.Add("")
[void]$lines.Add("| Leg | Task | Status | Opt-in recorded | cache_hit | Transcription (app) | Total wall (DB) |")
[void]$lines.Add("|---|---|---|---|---|---|---|")
foreach ($pair in @(@('A - separation OFF', $off), @('B - separation ON', $on))) {
    $leg = $pair[1]; $row = $leg.Row
    if ($row) {
        $tr = Get-TranscriptionSeconds $row
        $wall = Get-WallSeconds $row
        [void]$lines.Add("| $($pair[0]) | $($leg.TaskId) | $($row.Status) | $($row.OptIn) | $($row.CacheHit) | $(if ($null -ne $tr) { '{0:N1}s' -f $tr } else { 'NOT OBSERVED' }) | $(if ($null -ne $wall) { '{0:N1}s' -f $wall } else { 'NOT OBSERVED' }) |")
    } else {
        [void]$lines.Add("| $($pair[0]) | $($leg.TaskId) | NOT OBSERVED | - | - | - | - |")
    }
}
[void]$lines.Add("")
if ($offProblems.Count -gt 0) { [void]$lines.Add("LEG A INVALID: $($offProblems -join '; '). Its numbers below are printed for diagnosis only.") }
if ($onProblems.Count -gt 0) { [void]$lines.Add("LEG B INVALID: $($onProblems -join '; '). Its numbers below are printed for diagnosis only.") }
if ($offProblems.Count -eq 0 -and $onProblems.Count -eq 0) { [void]$lines.Add("Both legs valid: opt-in recorded as intended and neither leg reused a cached transcript.") }
[void]$lines.Add("")
[void]$lines.Add("## Hallucination probe - words inside the music-only window")
[void]$lines.Add("")
[void]$lines.Add("| Leg | Words in window | The words |")
[void]$lines.Add("|---|---|---|")
foreach ($pair in @(@('A - OFF', $off), @('B - ON', $on))) {
    $leg = $pair[1]
    if ($null -eq $leg.TranscriptWords) {
        [void]$lines.Add("| $($pair[0]) | NOT OBSERVED | transcript cache could not be read for this leg |")
    } else {
        $wordText = if ($leg.TranscriptWords -eq 0) { '(none)' } else { ($leg.TranscriptWordList -join ' ') }
        [void]$lines.Add("| $($pair[0]) | $($leg.TranscriptWords) | $wordText |")
    }
}
[void]$lines.Add("")
[void]$lines.Add("Reading: the window contains no speech, so every word in leg A is a hallucination the raw pipeline invented. Leg B's count is the decisive figure - zero means the stem-fed transcript stayed silent where there is only music.")
[void]$lines.Add("")
[void]$lines.Add("## Export attenuation in the music window")
[void]$lines.Add("")
[void]$lines.Add("| Leg | Clip measured | Overlap measured | Mean volume in window |")
[void]$lines.Add("|---|---|---|---|")
foreach ($pair in @(@('A - OFF', $off), @('B - ON', $on))) {
    $leg = $pair[1]
    if ($leg.AttenuationClip) {
        [void]$lines.Add("| $($pair[0]) | $($leg.AttenuationClip) | $('{0:N1}s of the window' -f $leg.AttenuationOverlap.Seconds) | $(Format-MeanDb $leg.AttenuationMeanDb) |")
    } else {
        [void]$lines.Add("| $($pair[0]) | NOT OBSERVED | no exported clip overlaps the music window | - |")
    }
}
[void]$lines.Add("")
if ($null -ne $attenuationDb) {
    [void]$lines.Add("Attenuation (A minus B): $('{0:N1} dB' -f $attenuationDb). Guidance: >= 20 dB - the strip is clearly removing the source music (the scoping's real-model bar; the in-tree synthetic proof measured 48.1 dB). 6-20 dB - material but modest. < 6 dB - no material difference observed in this window.")
} else {
    [void]$lines.Add("Attenuation: NOT OBSERVED - one or both legs produced no measurable overlap. Export a clip covering the music window in both tasks and re-run, or read the two mean volumes above if only one leg is missing its pair.")
}
[void]$lines.Add("")
[void]$lines.Add("## Separation activity in the worker log (whole scenario)")
[void]$lines.Add("")
[void]$lines.Add("- 'Separating vocals' runs: $sepRuns (device: cuda=$sepDeviceCuda cpu=$sepDeviceCpu); completed: $sepDone; stem actually transcribed: $sepStemUsed")
$sepRows = @($vramSummary | Where-Object { $_.Stage -like 'separation*' })
if ($sepRows.Count -gt 0) {
    foreach ($row in $sepRows) {
        [void]$lines.Add("- vram $($row.Stage): first free $($row.FirstFreeMb) MB, min free $($row.MinFreeMb) MB (total $($row.TotalMb) MB, $($row.Samples) samples)")
    }
} else {
    [void]$lines.Add("- VRAM telemetry at separation boundaries: NOT OBSERVED (no 'vram stage=separation_*' lines in the worker log)")
}
[void]$lines.Add("")
[void]$lines.Add("## Whole-machine sampler")
[void]$lines.Add("")
if ($sampleStats.Sampled) {
    [void]$lines.Add("- Samples: $($sampleStats.Samples); peak VRAM used: $($sampleStats.VramPeakMb) MB of $($sampleStats.VramTotalMb) MB; GPU util mean/peak: $($sampleStats.GpuUtilMean)% / $($sampleStats.GpuUtilPeak)%")
} else {
    [void]$lines.Add("NOT SAMPLED - sampler produced no rows.")
}
[void]$lines.Add("")
[void]$lines.Add("## Honesty notes")
[void]$lines.Add("")
[void]$lines.Add("- Leg B's transcription time includes the separation run itself (separation happens inside the transcription stage); the wall-time difference between legs is the honest end-to-end cost of opting in on this source.")
[void]$lines.Add("- The attenuation figure compares each task's most-overlapping exported clip, not a forced identical range: if the two tasks chose different moments, the overlap column shows exactly how much of the window each measurement covers.")
[void]$lines.Add("- Word counting uses each task's own transcript cache (word midpoints inside the window). A leg whose cache could not be read says NOT OBSERVED rather than zero.")

$reportPath = Join-Path $runDir 'report.md'
($lines -join "`r`n") + "`r`n" | Set-Content -Path $reportPath -Encoding UTF8
Write-Host ""
Write-Host "Report written: $reportPath" -ForegroundColor Green
