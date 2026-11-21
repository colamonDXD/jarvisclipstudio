<#
.SYNOPSIS
  SnipNova benchmark pack - VRAM collision scenario for the RTX 4050 (6 GB).

.DESCRIPTION
  Exercises the one genuinely likely VRAM collision from the Wave-4
  scoping (research_notes/wave4-vram/SCOPING.md section 3) on the real
  machine, in the order the pipeline meets it:

    Step 1  A normal faster-whisper task. The whisper model loads and
            then STAYS RESIDENT in the worker for the rest of the run —
            that residency is the precondition for everything after.
    Step 2  A second task while whisper is resident, with the provider
            manager in local-first mode: classification tries the local
            Ollama model (qwen3:8b, ~5.5 GB) next to the resident
            whisper model (~2-3.5 GB) on a 6 GB card.
    Step 3  A third task with voice/music separation opted in: Demucs
            chooses its device while the previous stages' residency is
            still in play.

  The app's own Stage-0 telemetry lines (backend/src/media/vram.py:
  'vram stage=<name> free_mb=<n> total_mb=<n>') are harvested from the
  worker log afterwards, so the headroom at every stage boundary is the
  app's own measurement, and sampler.ps1 records the whole-machine
  trace alongside it. A stage with no telemetry line reports
  NOT OBSERVED — nothing is estimated.

  Setup (do this BEFORE running the script, in the app root):
    $env:PROVIDER_MODE = 'local-first'
    $env:SEPARATION_ENABLED = 'true'
    docker compose up -d
  Both variables must be set from the start: PROVIDER_MODE is read when
  the worker process starts, and restarting the worker mid-scenario
  would evict the resident whisper model the scenario exists to test.
  Ollama must be running on the host with qwen3:8b pulled. Nothing else
  may be generating while the scenario runs.

  Then, from this folder:
    .\vram-scenario.ps1
  The script samples resources, walks you through the three tasks with
  pauses, harvests the worker log, and writes one report into
  benchmark-runs\vram-<timestamp>\ (report.md, resources.csv,
  worker-log.txt).
#>
[CmdletBinding()]
param(
    [string]$ProjectDir = "",
    [int]$SampleSeconds = 2
)

$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'benchmark-lib.ps1')

if (-not $ProjectDir) { $ProjectDir = (Resolve-Path (Join-Path $here '..\..')).Path }
$appRoot = $ProjectDir
if (-not (Test-Path (Join-Path $appRoot 'docker-compose.yml'))) {
    throw "No docker-compose.yml in $appRoot - point -ProjectDir at the app root."
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$runDir = Join-Path $ProjectDir (Join-Path 'benchmark-runs' "vram-$stamp")
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$csvPath = Join-Path $runDir 'resources.csv'
$stopFile = Join-Path $runDir 'sampler.stop'

Write-Host ""
Write-Host "VRAM collision scenario - RTX 4050 (6 GB)" -ForegroundColor Cyan
Write-Host "Run folder: $runDir"
Write-Host ""
Write-Host "Preconditions (see the header of this script for the exact commands):"
Write-Host "  - Stack started with PROVIDER_MODE=local-first and SEPARATION_ENABLED=true"
Write-Host "  - Ollama running on the host, qwen3:8b pulled"
Write-Host "  - Nothing else generating"
Write-Host ""
$go = Read-Host "Type GO when the stack is up that way"
if ($go -ne 'GO') { Write-Host "Aborted."; return }

# Start the whole-machine sampler for the duration of the scenario.
$sampler = Start-Process powershell -PassThru -WindowStyle Hidden -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $here 'sampler.ps1'),
    '-OutCsv', $csvPath, '-StopFile', $stopFile, '-IntervalSeconds', "$SampleSeconds"
)

function Wait-Step([string]$Text) {
    Write-Host ""
    Write-Host $Text -ForegroundColor Yellow
    [void](Read-Host "Press Enter when that task has finished processing")
}

try {
    Wait-Step "STEP 1/3 - Create a task on any short video, default settings (separation OFF). Wait for it to finish: faster-whisper is now resident in the worker."
    Wait-Step "STEP 2/3 - Create a task on a SECOND, new video (one the app has never processed). Classification now attempts the local model while whisper is resident. Wait for it to finish."
    Wait-Step "STEP 3/3 - Create a task on a THIRD new video and tick the voice/music separation opt-in. Wait for it to finish."
} finally {
    New-Item -ItemType File -Force -Path $stopFile | Out-Null
    if ($sampler -and -not $sampler.HasExited) { $sampler | Wait-Process -Timeout 30 -ErrorAction SilentlyContinue }
}

# Harvest the worker log and pick out the app's own telemetry + the
# Stage-1 fix markers (whisper eviction, Demucs device choice).
$logPath = Join-Path $runDir 'worker-log.txt'
$logLines = @()
try {
    Push-Location $appRoot
    $logLines = @(docker compose logs --no-color worker 2>$null)
    Pop-Location
} catch {
    Write-Host "Could not read worker logs via docker compose: $_" -ForegroundColor Red
}
$logLines | Set-Content -Path $logPath -Encoding UTF8

$summary = @(Get-VramStageSummary -Lines $logLines)
$sampleStats = Read-SampleStats -CsvPath $csvPath
$evictLines = @($logLines | Where-Object { $_ -match 'Released \d+ cached faster-whisper' }).Count
$unloadLines = @($logLines | Where-Object { $_ -match 'keep_alive' }).Count
$demucsCuda = @($logLines | Where-Object { $_ -match 'Separating vocals.*device=cuda' }).Count
$demucsCpu = @($logLines | Where-Object { $_ -match 'Separating vocals.*device=cpu' }).Count
$demucsRetry = @($logLines | Where-Object { $_ -match 'retrying once on CPU' }).Count

$lines = New-Object System.Collections.ArrayList
[void]$lines.Add("# VRAM collision scenario - $stamp")
[void]$lines.Add("")
[void]$lines.Add("Machine scenario: whisper resident -> local-LLM classification attempt -> separation (Wave-4 scoping section 3).")
[void]$lines.Add("Free VRAM figures are the app's own Stage-0 telemetry (worker log); whole-machine figures are sampler.ps1's.")
[void]$lines.Add("")
[void]$lines.Add("## Free VRAM at stage boundaries (app telemetry)")
[void]$lines.Add("")
if ($summary.Count -eq 0) {
    [void]$lines.Add("NOT OBSERVED - no 'vram stage=' telemetry lines in the worker log. Check the worker is running a build with Stage-0 telemetry and that torch/CUDA is visible inside the container.")
} else {
    [void]$lines.Add("| Stage | Samples | First free (MB) | Min free (MB) | Total (MB) |")
    [void]$lines.Add("|---|---|---|---|---|")
    foreach ($row in $summary) {
        [void]$lines.Add("| $($row.Stage) | $($row.Samples) | $($row.FirstFreeMb) | $($row.MinFreeMb) | $($row.TotalMb) |")
    }
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
[void]$lines.Add("## Stage-1 fix markers in the worker log")
[void]$lines.Add("")
[void]$lines.Add("- Whisper cache evictions ('Released N cached faster-whisper model(s)'): $evictLines")
[void]$lines.Add("- Ollama keep_alive unload requests logged: $unloadLines")
[void]$lines.Add("- Separation device choices: cuda=$demucsCuda cpu=$demucsCpu; CUDA->CPU retries: $demucsRetry")
[void]$lines.Add("")
[void]$lines.Add("## How to read this")
[void]$lines.Add("")
[void]$lines.Add("- classification_start free VRAM is what the local model's load had to fit into. Before the Stage-1 fixes this line would show whisper still resident; after them, an eviction marker should appear just before it and free VRAM should jump.")
[void]$lines.Add("- separation_start free VRAM decides Demucs' device: below the ~2.2 GB headroom gate it must choose cpu directly (see 'Separating vocals ... device=' in worker-log.txt), never a doomed cuda attempt.")
[void]$lines.Add("- Judge outcomes in the app: step 2's task classification (which model was used is on the task's classification record) and step 3's transcript quality. This report deliberately does not invent a pass/fail for those.")

$reportPath = Join-Path $runDir 'report.md'
($lines -join "`r`n") + "`r`n" | Set-Content -Path $reportPath -Encoding UTF8
Write-Host ""
Write-Host "Report written: $reportPath" -ForegroundColor Green
