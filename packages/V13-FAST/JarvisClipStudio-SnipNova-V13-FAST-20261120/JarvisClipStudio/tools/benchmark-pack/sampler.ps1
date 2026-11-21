<#
.SYNOPSIS
  SnipNova benchmark pack - resource sampler.

.DESCRIPTION
  Polls machine resources on an interval and appends rows to a CSV until
  a stop file appears (or -MaxMinutes elapses). Started and stopped by
  run-benchmark-pack.ps1; can also be run standalone.

  Columns: timestamp,gpu_util_pct,gpu_mem_used_mb,gpu_mem_total_mb,
           cpu_pct,ram_used_gb,ram_total_gb

  Probes:
  - GPU: nvidia-smi --query-gpu (blank cells when nvidia-smi is absent).
  - CPU: Get-Counter '\Processor(_Total)\% Processor Time', falling back
    to Win32_Processor LoadPercentage.
  - RAM: Win32_OperatingSystem visible memory.
  Any probe that fails leaves its cells blank - the aggregator in
  benchmark-lib.ps1 skips blanks per metric. Nothing is estimated.

  -Mock replaces every probe with deterministic pseudo-values. It exists
  ONLY to validate the loop/CSV/stop-file mechanics off-machine; a mock
  CSV is watermarked by its values being synthetic and must never be
  reported as a measurement.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$OutCsv,
    [Parameter(Mandatory = $true)][string]$StopFile,
    [int]$IntervalSeconds = 5,
    [int]$MaxMinutes = 180,
    [switch]$Mock
)

$ErrorActionPreference = "Continue"

function Get-GpuSample {
    if ($Mock) { return @(87, 3410, 6144) }
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if (-not $smi) { return @('', '', '') }
    try {
        $line = (& nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits 2>$null | Select-Object -First 1)
        if (-not $line) { return @('', '', '') }
        $p = $line -split ',' | ForEach-Object { $_.Trim() }
        return @([int]$p[0], [int]$p[1], [int]$p[2])
    } catch { return @('', '', '') }
}

function Get-CpuSample {
    if ($Mock) { return 64 }
    try {
        $c = Get-Counter '\Processor(_Total)\% Processor Time' -SampleInterval 1 -MaxSamples 1 -ErrorAction Stop
        return [math]::Round($c.CounterSamples[0].CookedValue, 1)
    } catch {
        try {
            $p = Get-CimInstance Win32_Processor -ErrorAction Stop | Measure-Object -Property LoadPercentage -Average
            return [math]::Round($p.Average, 1)
        } catch { return '' }
    }
}

function Get-RamSample {
    if ($Mock) { return @(10.8, 16.0) }
    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        $totalGb = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
        $usedGb = [math]::Round(($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / 1MB, 1)
        return @($usedGb, $totalGb)
    } catch { return @('', '') }
}

$dir = Split-Path -Parent $OutCsv
if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
if (-not (Test-Path $OutCsv)) {
    'timestamp,gpu_util_pct,gpu_mem_used_mb,gpu_mem_total_mb,cpu_pct,ram_used_gb,ram_total_gb' | Set-Content -Path $OutCsv -Encoding ASCII
}

$deadline = (Get-Date).AddMinutes($MaxMinutes)
while ((Get-Date) -lt $deadline) {
    if (Test-Path $StopFile) { break }
    $gpu = Get-GpuSample
    $cpu = Get-CpuSample
    $ram = Get-RamSample
    $row = "{0},{1},{2},{3},{4},{5},{6}" -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'), $gpu[0], $gpu[1], $gpu[2], $cpu, $ram[0], $ram[1]
    Add-Content -Path $OutCsv -Value $row -Encoding ASCII
    # Sleep in short slices so the stop file is honoured promptly; the CPU
    # counter probe itself consumes ~1s of the interval.
    $slept = 0
    while ($slept -lt $IntervalSeconds) {
        if (Test-Path $StopFile) { break }
        Start-Sleep -Seconds 1
        $slept++
    }
}
