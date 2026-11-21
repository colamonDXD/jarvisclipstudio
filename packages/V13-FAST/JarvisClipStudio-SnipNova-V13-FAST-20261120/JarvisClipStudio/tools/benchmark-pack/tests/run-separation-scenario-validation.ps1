<#
.SYNOPSIS
  Remote validation for the separation scenario's pure logic (benchmark-lib.ps1).

.DESCRIPTION
  Runs anywhere PowerShell runs - no Windows, no Docker, no GPU needed.
  Exercises the four helpers separation-scenario.ps1 depends on:
  ConvertFrom-MmSs (generated_clips timestamps), Get-WindowOverlap
  (clip range vs music window, clip-local seconds), Get-VolumeDetectMean
  (ffmpeg volumedetect parsing, including digital silence), and
  Get-TranscriptWindowWords (transcript-cache word timing in ms vs a
  window in seconds).
  Every assertion prints PASS/FAIL; the script exits non-zero on any
  FAIL. These are SYNTHETIC/logic tests - they say nothing about
  separation quality or timings on the user's machine.
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

# ---- 1. MM:SS parsing ---------------------------------------------------
Assert ((ConvertFrom-MmSs '28:16') -eq 1696) 'MM:SS parses (28:16 = 1696)'
Assert ((ConvertFrom-MmSs '07:05') -eq 425) 'zero-padded MM:SS parses'
Assert ((ConvertFrom-MmSs '00:00') -eq 0) 'zero timestamp parses'
Assert ((ConvertFrom-MmSs '1:02:03') -eq 3723) 'H:MM:SS tolerated'
Assert ($null -eq (ConvertFrom-MmSs 'abc')) 'garbage rejected'
Assert ($null -eq (ConvertFrom-MmSs '')) 'empty rejected'
Assert ($null -eq (ConvertFrom-MmSs '12')) 'single number rejected (not a timestamp)'
Assert ($null -eq (ConvertFrom-MmSs '12:xx')) 'non-numeric part rejected'

# ---- 2. window overlap --------------------------------------------------
$full = Get-WindowOverlap -ClipStart 100 -ClipEnd 200 -WindowStart 120 -WindowEnd 160
Assert ($null -ne $full -and $full.Seconds -eq 40) 'contained window: full overlap seconds'
Assert ($full.LocalStart -eq 20 -and $full.LocalEnd -eq 60) 'contained window: clip-local start/end'

$left = Get-WindowOverlap -ClipStart 100 -ClipEnd 200 -WindowStart 40 -WindowEnd 130
Assert ($left.Seconds -eq 30 -and $left.LocalStart -eq 0) 'window starting before clip clamps to clip start'

$right = Get-WindowOverlap -ClipStart 100 -ClipEnd 200 -WindowStart 190 -WindowEnd 300
Assert ($right.Seconds -eq 10 -and $right.LocalEnd -eq 100) 'window ending after clip clamps to clip end'

Assert ($null -eq (Get-WindowOverlap -ClipStart 100 -ClipEnd 200 -WindowStart 200 -WindowEnd 300)) 'touching at the edge is no overlap'
Assert ($null -eq (Get-WindowOverlap -ClipStart 100 -ClipEnd 200 -WindowStart 300 -WindowEnd 400)) 'disjoint window rejected'

# ---- 3. volumedetect parsing --------------------------------------------
$volText = @'
[Parsed_volumedetect_0 @ 0x55f0] n: 10584000
[Parsed_volumedetect_0 @ 0x55f0] mean_volume: -27.3 dB
[Parsed_volumedetect_0 @ 0x55f0] max_volume: -1.2 dB
'@
Assert ((Get-VolumeDetectMean -Text $volText) -eq -27.3) 'mean_volume parsed, max_volume not mistaken for it'
$silence = Get-VolumeDetectMean -Text '[Parsed_volumedetect_0] mean_volume: -inf dB'
Assert ([double]::IsNegativeInfinity($silence)) 'digital silence parses to negative infinity (a measurement, not a failure)'
Assert ($null -eq (Get-VolumeDetectMean -Text 'ffmpeg output without the filter line')) 'missing mean_volume = null (NOT OBSERVED)'

# ---- 4. transcript words in window --------------------------------------
$cache = [pscustomobject]@{
    words = @(
        [pscustomobject]@{ text = 'hello'; start = 1000; end = 1500 },     # mid 1.25 s
        [pscustomobject]@{ text = 'music'; start = 30000; end = 31000 },   # mid 30.5 s
        [pscustomobject]@{ text = 'phantom'; start = 44900; end = 45100 }, # mid 45.0 s
        [pscustomobject]@{ text = 'edge'; start = 54000; end = 56000 },    # mid 55.0 s = window end (excluded)
        [pscustomobject]@{ text = 'late'; start = 60000; end = 61000 }     # outside
    )
}
$inWindow = @(Get-TranscriptWindowWords -Cache $cache -WindowStart 30 -WindowEnd 55)
Assert ($inWindow.Count -eq 2) 'only window words counted (ms timings, midpoint rule, end exclusive)'
Assert ($inWindow[0] -eq 'music' -and $inWindow[1] -eq 'phantom') 'window words returned in order'
$none = @(Get-TranscriptWindowWords -Cache $cache -WindowStart 200 -WindowEnd 300)
Assert ($none.Count -eq 0) 'empty window = zero words (a real result, not NOT OBSERVED)'
$empty = @(Get-TranscriptWindowWords -Cache $null -WindowStart 0 -WindowEnd 10)
Assert ($empty.Count -eq 0) 'missing cache object yields no words (caller reports NOT OBSERVED)'

Write-Host ""
if ($failures -gt 0) { Write-Host "$failures assertion(s) FAILED"; exit 1 }
Write-Host "All assertions passed."
