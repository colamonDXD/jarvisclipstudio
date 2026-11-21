<#
.SYNOPSIS
  Remote validation for the benchmark pack's pure logic (benchmark-lib.ps1).

.DESCRIPTION
  Runs anywhere PowerShell runs - no Windows, no Docker, no GPU needed.
  Exercises:
    1. Clip quality checks against a REAL ffprobe JSON captured from a
       quality-milestone export (runs/after-quality/r2.mp4, 1080x1920).
    2. Loudness parse against REAL ffmpeg loudnorm output for the same clip.
    3. Sampler CSV aggregation on fabricated sample CSVs (incl. blank cells).
    4. Full report generation TWICE: run 1 establishes baseline.json,
       run 2 must produce a comparison section with correct deltas and
       must NOT overwrite the baseline.
  Every assertion prints PASS/FAIL; the script exits non-zero on any FAIL.
  These are SYNTHETIC/logic tests (plus real-artifact inputs) - they say
  nothing about performance numbers on the user's machine.
#>
[CmdletBinding()]
param([string]$WorkDir = "")

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$packDir = Split-Path -Parent $here
. (Join-Path $packDir 'benchmark-lib.ps1')

if (-not $WorkDir) { $WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) ("benchpack-validate-" + [guid]::NewGuid().ToString('N')) }
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$sample = Join-Path $here 'sample-data'
$failures = 0
function Assert([bool]$Cond, [string]$Name) {
    if ($Cond) { Write-Host "PASS: $Name" } else { Write-Host "FAIL: $Name"; $script:failures++ }
}

# ---- 1. probe checks on the real export --------------------------------
$probe = Get-Content (Join-Path $sample 'probe-r2.json') -Raw | ConvertFrom-Json
$checks = Get-ProbeChecks -Probe $probe -DbDuration 27.0
$byName = @{}; foreach ($c in $checks) { $byName[$c.Name] = $c }
Assert ($byName['Resolution'].Status -eq 'PASS') 'real export: resolution 1080x1920 PASS'
Assert ($byName['Video codec'].Status -eq 'PASS') 'real export: codec h264 PASS'
Assert ($byName['Audio stream'].Status -eq 'PASS') 'real export: audio aac PASS'
Assert ($byName['Duration vs plan'].Status -eq 'PASS') 'real export: duration matches plan PASS'
Assert ($byName['A/V container start offset'].Status -eq 'PASS') 'real export: A/V start offset PASS'
Assert ($byName['Encoder'].Status -eq 'WARN') 'real export: libx264 encoder honestly WARN (sandbox CPU render, not NVENC)'

# negative control: shrink the probe resolution -> must FAIL
$bad = Get-Content (Join-Path $sample 'probe-r2.json') -Raw | ConvertFrom-Json
$bad.streams[0].width = 720; $bad.streams[0].height = 1280
$badChecks = Get-ProbeChecks -Probe $bad -DbDuration 27.0
$badRes = ($badChecks | Where-Object { $_.Name -eq 'Resolution' }).Status
Assert ($badRes -eq 'FAIL') 'negative control: 720x1280 resolution FAILs'

# missing planned duration -> NOT OBSERVED, never guessed
$noDb = Get-ProbeChecks -Probe $probe
Assert ((($noDb | Where-Object { $_.Name -eq 'Duration vs plan' }).Status) -eq 'NOT OBSERVED') 'no planned duration -> NOT OBSERVED'

# ---- 2. loudness on the real loudnorm output ----------------------------
$lufs = Get-LoudnormIntegrated -Text (Get-Content (Join-Path $sample 'loudnorm-r2.txt') -Raw)
Assert ($null -ne $lufs) 'loudnorm: integrated value parsed from real ffmpeg output'
Assert ([math]::Abs($lufs - (-15.57)) -lt 0.01) "loudnorm: value is -15.57 LUFS (got $lufs)"
$lc = Get-LoudnessCheck -Lufs $lufs
Assert ($lc.Status -eq 'PASS') 'loudnorm: -15.57 within -14 +/-2 band PASS'
Assert ((Get-LoudnessCheck -Lufs (-9.0)).Status -eq 'FAIL') 'loudnorm: -9 LUFS FAILs the band'
Assert ($null -eq (Get-LoudnormIntegrated -Text 'no json here')) 'loudnorm: garbage input -> null, not a number'

# ---- 3. sampler aggregation ---------------------------------------------
$s1 = Read-SampleStats -CsvPath (Join-Path $sample 'resources-run1.csv')
Assert ($s1.Sampled -and $s1.Samples -eq 5) 'samples run1: 5 rows counted'
Assert ($s1.GpuUtilPeak -eq 95) 'samples run1: GPU peak 95 (blank row skipped)'
Assert ($s1.VramPeakMb -eq 4022) 'samples run1: VRAM peak 4022 MB'
Assert ($s1.RamUsedPeakGb -eq 11.6) 'samples run1: RAM peak 11.6 GB'
$sMissing = Read-SampleStats -CsvPath (Join-Path $sample 'does-not-exist.csv')
Assert (-not $sMissing.Sampled) 'samples: missing CSV -> NOT SAMPLED'

# ---- 4. report generation: baseline then comparison ----------------------
function New-TestData([string]$TimingsFile, [string]$CsvFile, [string]$Stamp) {
    $timings = Get-Content (Join-Path $sample $TimingsFile) -Raw | ConvertFrom-Json
    $rows = Get-StageRows -Timings $timings -TotalWallSeconds 560.0
    $stats = Read-SampleStats -CsvPath (Join-Path $sample $CsvFile)
    $clipChecks = @(Get-ProbeChecks -Probe $probe -DbDuration 27.0)
    $clipChecks += Get-LoudnessCheck -Lufs $lufs
    return @{
        Title = 'SnipNova benchmark - remote validation fixture'
        GeneratedAt = $Stamp
        EvidenceNote = 'SYNTHETIC validation run - fabricated timings, real probe/loudnorm inputs. Not machine performance data.'
        Specs = @('GPU: NVIDIA GeForce RTX 4050 Laptop GPU (6144 MB)', 'CPU: AMD Ryzen 7 7445HS', 'RAM: 16.0 GB')
        Workload = @('Fixture: JRE bake-off video (fixed)', 'Niche: podcast_news (manual)', 'Target clips: 5')
        CacheHit = $false
        StageRows = $rows
        SampleStats = $stats
        ClipReports = @(@{ Clip = 'Clip 1 (r2.mp4)'; Checks = $clipChecks })
        FramesNote = $null
        DiskBeforeGb = 120.5; DiskAfterGb = 119.9
        RunDir = $WorkDir
    }
}

$reportDir = Join-Path $WorkDir 'runs'
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
$d1 = New-TestData 'timings-run1.json' 'resources-run1.csv' '2026-11-09 20:00 IST'
$r1Path = Join-Path $reportDir 'report-run1.md'
$text1 = New-BenchmarkReport -Data $d1 -ReportPath $r1Path -BaselineDir $reportDir
Assert (Test-Path $r1Path) 'report run1: file written'
Assert (Test-Path (Join-Path $reportDir 'baseline.json')) 'report run1: baseline.json established'
Assert ($text1 -match 'stored as baseline\.json') 'report run1: says baseline was established'
Assert ($text1 -match 'Transcription \(faster-whisper\) \| 96\.5s') 'report run1: transcription stage row present with value'
$baseBefore = Get-Content (Join-Path $reportDir 'baseline.json') -Raw

$d2 = New-TestData 'timings-run2.json' 'resources-run2.csv' '2026-11-16 20:00 IST'
$r2Path = Join-Path $reportDir 'report-run2.md'
$text2 = New-BenchmarkReport -Data $d2 -ReportPath $r2Path -BaselineDir $reportDir
Assert ($text2 -match 'Comparison vs stored baseline') 'report run2: comparison section present'
Assert ($text2 -match 'total_wall_s') 'report run2: total wall row compared'
Assert ($text2 -match 'gpu_util_peak_pct \| 95 \| 99') 'report run2: GPU peak 95 -> 99 compared'
Assert ($text2 -match 'first_clip_lufs') 'report run2: loudness compared'
Assert ((Get-Content (Join-Path $reportDir 'baseline.json') -Raw) -eq $baseBefore) 'report run2: baseline NOT overwritten without -SetBaseline'

# a run whose timings lack a stage -> NOT OBSERVED in the table
$sparse = Get-StageRows -Timings (@{} | ConvertTo-Json | ConvertFrom-Json)
Assert ((($sparse | Where-Object { $_.Stage -like 'Download*' }).Display) -eq 'NOT OBSERVED') 'stages: missing key -> NOT OBSERVED'

Write-Host ''
if ($failures -gt 0) { Write-Host "VALIDATION FAILED: $failures assertion(s)" -ForegroundColor Red; exit 1 }
Write-Host "VALIDATION OK - all assertions passed. Work dir: $WorkDir" -ForegroundColor Green
