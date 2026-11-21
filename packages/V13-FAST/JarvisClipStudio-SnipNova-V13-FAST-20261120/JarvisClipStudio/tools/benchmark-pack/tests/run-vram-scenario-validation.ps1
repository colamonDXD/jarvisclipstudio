<#
.SYNOPSIS
  Remote validation for the VRAM scenario's pure logic (benchmark-lib.ps1).

.DESCRIPTION
  Runs anywhere PowerShell runs - no Windows, no Docker, no GPU needed.
  Exercises ConvertFrom-VramTelemetryLine and Get-VramStageSummary
  against fabricated app log lines in the exact Stage-0 format emitted
  by backend/src/media/vram.py, including log-prefix noise, the
  'free_mb=unavailable' DEBUG form (no measurement - must be ignored),
  and out-of-order stage lines.
  Every assertion prints PASS/FAIL; the script exits non-zero on any
  FAIL. These are SYNTHETIC/logic tests - they say nothing about VRAM
  numbers on the user's machine.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$packDir = Split-Path -Parent $here
. (Join-Path $packDir 'benchmark-lib.ps1')

$failures = 0
function Assert([bool]$Cond, [string]$Name) {
    if ($Cond) { Write-Host "PASS: $Name" } else { Write-Host "FAIL: $Name"; $script:failures++ }
}

# ---- 1. line parsing ----------------------------------------------------
$good = ConvertFrom-VramTelemetryLine '2026-11-11 10:00:00 INFO src.media.vram vram stage=transcription_start free_mb=3812 total_mb=6144'
Assert ($null -ne $good) 'telemetry line with log prefix parses'
Assert ($good.Stage -eq 'transcription_start') 'stage name parsed'
Assert ($good.FreeMb -eq 3812) 'free_mb parsed as int'
Assert ($good.TotalMb -eq 6144) 'total_mb parsed as int'

$bare = ConvertFrom-VramTelemetryLine 'vram stage=separation_end free_mb=1024 total_mb=6144'
Assert ($null -ne $bare -and $bare.Stage -eq 'separation_end') 'bare telemetry line parses'

Assert ($null -eq (ConvertFrom-VramTelemetryLine 'some unrelated worker log line')) 'noise line rejected'
Assert ($null -eq (ConvertFrom-VramTelemetryLine 'vram stage=transcription_start free_mb=unavailable')) 'unavailable DEBUG line rejected (no measurement)'
Assert ($null -eq (ConvertFrom-VramTelemetryLine '')) 'empty line rejected'
Assert ($null -eq (ConvertFrom-VramTelemetryLine 'vram stage=x free_mb=12 total_mb=')) 'truncated line rejected'

# ---- 2. stage summary aggregation ---------------------------------------
$log = @(
    'noise before',
    'vram stage=transcription_start free_mb=5900 total_mb=6144',
    'vram stage=transcription_end free_mb=2400 total_mb=6144',
    'vram stage=classification_start free_mb=2350 total_mb=6144',
    'vram stage=classification_end free_mb=900 total_mb=6144',
    'vram stage=transcription_start free_mb=5800 total_mb=6144',
    'vram stage=transcription_end free_mb=2300 total_mb=6144',
    'vram stage=transcription_start free_mb=100 total_mb=6144'
)
$summary = @(Get-VramStageSummary -Lines $log)
Assert ($summary.Count -eq 4) 'summary has one row per distinct stage'
Assert ($summary[0].Stage -eq 'transcription_start') 'rows keep first-seen stage order'
$ts = $summary | Where-Object { $_.Stage -eq 'transcription_start' }
Assert ($ts.Samples -eq 3) 'repeated stage samples counted'
Assert ($ts.FirstFreeMb -eq 5900) 'first free kept from the first occurrence'
Assert ($ts.MinFreeMb -eq 100) 'min free tracked across occurrences'
$ce = $summary | Where-Object { $_.Stage -eq 'classification_end' }
Assert ($ce.MinFreeMb -eq 900 -and $ce.Samples -eq 1) 'single-occurrence stage summarised'

$empty = @(Get-VramStageSummary -Lines @('nothing', 'here'))
Assert ($empty.Count -eq 0) 'no telemetry lines = empty summary (NOT OBSERVED is the caller''s wording)'

Write-Host ""
if ($failures -gt 0) { Write-Host "$failures assertion(s) FAILED"; exit 1 }
Write-Host "All assertions passed."
