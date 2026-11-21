<#
.SYNOPSIS
  SnipNova benchmark pack - one comparable, honest benchmark run.

.DESCRIPTION
  Run from PowerShell on the machine where the app is installed
  (Windows, Docker stack up). The pack:

    1. Records machine specs (GPU, CPU, RAM, .wslconfig limits, Docker,
       disk space, build stamp).
    2. Runs the FIXED workload defined in FIXTURE.md. Two ways in:
       - Guided (default): the script prints the exact fixture + settings,
         then watches the database for the new task you start in the app
         UI, and samples resources live while it processes.
       - Attach: -TaskId <id> samples a task that is still processing,
         or harvests a finished one (stage timings only - utilisation is
         then honestly NOT SAMPLED, never reconstructed).
    3. Samples GPU utilisation/VRAM, CPU and RAM every -SampleSeconds
       into resources.csv (sampler.ps1).
    4. Harvests the app's OWN stage timings (tasks.stage_timings_json:
       download/transcription/analysis/pipeline/render) and total wall
       time from the database - no parallel timing system.
    5. Quality-checks every produced clip with ffprobe inside the worker
       container (1080x1920, h264, AAC, duration vs plan, A/V container
       start offset), measures integrated loudness of the first clip
       (ffmpeg loudnorm, target -14 LUFS), and grabs 3 still frames from
       the first clip for eyeball checks.
    6. Writes ONE report (report.md + resources.csv + frames) into
       benchmark-runs\run-<timestamp>\ and compares against the stored
       baseline.json next to this script (first run establishes it;
       -SetBaseline replaces it deliberately).

  Safety: read-only against the app except the one benchmark task YOU
  create in the UI. It deletes nothing, restarts nothing, changes no
  config. Bounded: task detection waits at most -DetectTimeoutMinutes,
  processing at most -MaxWaitMinutes, then it reports what it has.

  Total expected time: fixture processing time (roughly 5-15 min for the
  14-minute fixture on an RTX 4050) plus ~1 min of checks.

.EXAMPLE
  cd D:\EditedTool\JarvisClipStudio\tools\benchmark-pack
  .\run-benchmark-pack.ps1
  .\run-benchmark-pack.ps1 -TaskId 368d0cda-....
  .\run-benchmark-pack.ps1 -SetBaseline
#>
[CmdletBinding()]
param(
    [string]$ProjectDir = "",
    [string]$TaskId = "",
    [int]$SampleSeconds = 5,
    [int]$MaxWaitMinutes = 120,
    [int]$DetectTimeoutMinutes = 20,
    [string]$OutRoot = "",
    [switch]$SetBaseline
)

$ErrorActionPreference = "Stop"
$packDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $packDir 'benchmark-lib.ps1')

if (-not $ProjectDir) { $ProjectDir = (Resolve-Path (Join-Path $packDir '..\..')).Path }
if (-not $OutRoot) { $OutRoot = Join-Path $ProjectDir 'benchmark-runs' }
Set-Location $ProjectDir
if (-not (Test-Path (Join-Path $ProjectDir 'docker-compose.yml'))) {
    throw "No docker-compose.yml in $ProjectDir - point -ProjectDir at the app root."
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$runDir = Join-Path $OutRoot ("run-" + $stamp)
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $runDir 'frames') | Out-Null
$samplesCsv = Join-Path $runDir 'resources.csv'
$stopFile = Join-Path $runDir 'sampler.stop'

function Invoke-Psql([string]$Sql) {
    $out = docker compose exec -T postgres psql -U supoclip -d supoclip -At -F "|" -c $Sql
    if ($LASTEXITCODE -ne 0) { throw "psql failed for: $Sql" }
    return ($out | Out-String).Trim()
}
function Get-TaskRow([string]$Id) {
    $row = Invoke-Psql "SELECT status, COALESCE(started_at::text,''), COALESCE(completed_at::text,''), COALESCE(stage_timings_json,''), COALESCE(cache_hit::text,'') FROM tasks WHERE id = '$Id';"
    if (-not $row) { return $null }
    $p = $row -split '\|', 5
    return [pscustomobject]@{ Status = $p[0]; StartedAt = $p[1]; CompletedAt = $p[2]; TimingsJson = $p[3]; CacheHit = $p[4] }
}

# ---------------------------------------------------------------- specs
Write-Host "=== SnipNova benchmark pack ===" -ForegroundColor Cyan
Write-Host "Run folder: $runDir"
Write-Host "Bounded runtime: detect <= ${DetectTimeoutMinutes} min, processing <= ${MaxWaitMinutes} min."
$specs = @()
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smi) {
    $g = (& nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>$null | Select-Object -First 1)
    if ($g) { $specs += "GPU: $g" }
} else { $specs += 'GPU: NOT OBSERVED (nvidia-smi not found)' }
try {
    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
    $specs += "CPU: $($cpu.Name) ($($cpu.NumberOfCores) cores / $($cpu.NumberOfLogicalProcessors) threads)"
    $os = Get-CimInstance Win32_OperatingSystem
    $specs += "RAM: $([math]::Round($os.TotalVisibleMemorySize / 1MB, 1)) GB total"
} catch { $specs += 'CPU/RAM: NOT OBSERVED (CIM unavailable)' }
$wslCfg = Join-Path $env:USERPROFILE '.wslconfig'
if (Test-Path $wslCfg) {
    $mem = (Select-String -Path $wslCfg -Pattern '^\s*memory\s*=\s*(.+)$').Matches.Groups[1].Value
    $swap = (Select-String -Path $wslCfg -Pattern '^\s*swap\s*=\s*(.+)$').Matches.Groups[1].Value
    $specs += "WSL limits (.wslconfig): memory=$mem swap=$swap"
} else { $specs += 'WSL limits: no .wslconfig found (Docker Desktop defaults apply)' }
try { $specs += "Docker: $(docker --version)" } catch { $specs += 'Docker: NOT OBSERVED' }
if (Test-Path (Join-Path $ProjectDir '.git')) {
    try { $specs += "Build: git $(git -C $ProjectDir rev-parse --short HEAD) on $(git -C $ProjectDir branch --show-current)" } catch { $specs += 'Build: git present, stamp unreadable' }
} else {
    $specs += 'Build: installed build (no .git in the install tree)'
}
$drive = (Get-Item $ProjectDir).PSDrive.Name
$diskBefore = [math]::Round((Get-PSDrive $drive).Free / 1GB, 1)
$specs += "Disk free on ${drive}: $diskBefore GB at start"

# ---------------------------------------------------------------- stack
try {
    $health = Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost:8000/health' -TimeoutSec 10
    if ($health.StatusCode -ne 200) { throw "backend health returned $($health.StatusCode)" }
} catch { throw "The app stack does not look up (http://localhost:8000/health). Start it first: docker compose up -d" }

# ---------------------------------------------------------------- task in
$attached = $false
if ($TaskId) {
    $row0 = Get-TaskRow $TaskId
    if (-not $row0) { throw "Task $TaskId not found in the database." }
    $attached = ($row0.Status -notin @('processing', 'queued', 'pending'))
    Write-Host "Attaching to task $TaskId (status: $($row0.Status))."
} else {
    Write-Host ''
    Write-Host '--- FIXED WORKLOAD (see FIXTURE.md) ---' -ForegroundColor Yellow
    Write-Host '1. Open the app: http://localhost:3107'
    Write-Host '2. New generation -> paste this source link:'
    Write-Host '     https://www.youtube.com/watch?v=z6S9DyxVwLg   (JRE, ~14 min)'
    Write-Host '   No network / already used it? Use the synthetic fixture instead'
    Write-Host '   (FIXTURE.md explains; a cached/repeated video invalidates the'
    Write-Host '   front-stage timings and the report will say so via cache_hit).'
    Write-Host '3. Settings: niche = Podcast & news (manual, NOT auto-detect),'
    Write-Host '   clips = 5, length = Medium (30-60s), Music = Off, quality = Full.'
    Write-Host '4. Start the generation, then leave this window open.'
    Write-Host "Waiting up to $DetectTimeoutMinutes min for a new task to appear..."
    $marker = Invoke-Psql "SELECT COALESCE(MAX(created_at)::text,'') FROM tasks;"
    if (-not $marker) { $marker = '1970-01-01 00:00:00' }
    $deadline = (Get-Date).AddMinutes($DetectTimeoutMinutes)
    while ((Get-Date) -lt $deadline) {
        $cand = Invoke-Psql "SELECT id FROM tasks WHERE created_at > '$marker'::timestamp ORDER BY created_at ASC LIMIT 1;"
        if ($cand) { $TaskId = $cand.Trim(); break }
        Start-Sleep -Seconds 10
    }
    if (-not $TaskId) { throw "No new task appeared within $DetectTimeoutMinutes min. Nothing was measured; no report written." }
    Write-Host "Detected benchmark task: $TaskId"
}

# ---------------------------------------------------------------- sample
$samplerJob = $null
if (-not $attached) {
    $samplerJob = Start-Job -FilePath (Join-Path $packDir 'sampler.ps1') -ArgumentList $samplesCsv, $stopFile, $SampleSeconds, ($MaxWaitMinutes + 10)
    Write-Host "Sampling resources every ${SampleSeconds}s -> $samplesCsv"
    $deadline = (Get-Date).AddMinutes($MaxWaitMinutes)
    $status = ''
    while ((Get-Date) -lt $deadline) {
        $r = Get-TaskRow $TaskId
        if (-not $r) { throw "Task $TaskId disappeared from the database." }
        $status = $r.Status
        if ($status -in @('completed', 'failed', 'error', 'cancelled')) { break }
        Start-Sleep -Seconds 10
    }
    New-Item -ItemType File -Force -Path $stopFile | Out-Null
    if ($samplerJob) { Wait-Job $samplerJob -Timeout 30 | Out-Null; Remove-Job $samplerJob -Force -ErrorAction SilentlyContinue }
    if ($status -notin @('completed', 'failed', 'error', 'cancelled')) {
        Write-Host "Task still '$status' after $MaxWaitMinutes min - harvesting what exists." -ForegroundColor Yellow
    }
} else {
    Write-Host 'Task already finished - stage timings only; utilisation NOT SAMPLED.' -ForegroundColor Yellow
}

# ---------------------------------------------------------------- harvest
$row = Get-TaskRow $TaskId
if (-not $row) { throw "Could not harvest task $TaskId." }
$timings = $null
if ($row.TimingsJson) { try { $timings = $row.TimingsJson | ConvertFrom-Json } catch { $timings = $null } }
$totalWall = -1
if ($row.StartedAt -and $row.CompletedAt) {
    $totalWall = ([datetime]$row.CompletedAt - [datetime]$row.StartedAt).TotalSeconds
}
$stageRows = Get-StageRows -Timings $timings -TotalWallSeconds $totalWall
$cacheHit = $null
if ($row.CacheHit -eq 'true') { $cacheHit = $true } elseif ($row.CacheHit -eq 'false') { $cacheHit = $false }

$clipRows = @()
$clipsRaw = Invoke-Psql "SELECT filename, file_path, duration FROM generated_clips WHERE task_id = '$TaskId' ORDER BY clip_order ASC;"
if ($clipsRaw) {
    foreach ($line in ($clipsRaw -split "`n")) {
        if (-not $line.Trim()) { continue }
        $p = $line -split '\|', 3
        $clipRows += [pscustomobject]@{ Filename = $p[0]; FilePath = $p[1]; Duration = [double]$p[2] }
    }
}
Write-Host "Clips produced: $($clipRows.Count)"

# ---------------------------------------------------------------- quality
$clipReports = @()
$framesNote = $null
$idx = 0
foreach ($clip in $clipRows) {
    $idx++
    $label = "Clip $idx ($($clip.Filename))"
    $checks = @()
    $probeJson = $null
    try {
        $probeOut = docker compose exec -T worker ffprobe -v error -show_streams -show_format -of json "$($clip.FilePath)"
        if ($LASTEXITCODE -eq 0 -and $probeOut) { $probeJson = ($probeOut | Out-String) | ConvertFrom-Json }
    } catch { $probeJson = $null }
    if ($probeJson) {
        $checks = @(Get-ProbeChecks -Probe $probeJson -DbDuration $clip.Duration)
    } else {
        $checks += New-CheckResult 'ffprobe' 'NOT OBSERVED' 'ffprobe inside the worker container failed for this clip'
    }
    if ($idx -eq 1 -and $probeJson) {
        try {
            $oldEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
            $loudOut = (docker compose exec -T worker ffmpeg -i "$($clip.FilePath)" -vn -af loudnorm=print_format=json -f null - 2>&1 | Out-String)
            $ErrorActionPreference = $oldEap
            $lufs = Get-LoudnormIntegrated -Text $loudOut
            if ($null -ne $lufs) { $checks += Get-LoudnessCheck -Lufs $lufs }
            else { $checks += New-CheckResult 'Loudness' 'NOT OBSERVED' 'loudnorm output could not be parsed' }
        } catch { $checks += New-CheckResult 'Loudness' 'NOT OBSERVED' 'ffmpeg loudnorm run failed' }
        # 3 still frames for eyeball checks
        try {
            $dur = $clip.Duration
            $times = @([math]::Round($dur * 0.1, 1), [math]::Round($dur * 0.5, 1), [math]::Round($dur * 0.9, 1))
            $n = 0
            foreach ($t in $times) {
                $n++
                $tmpName = "bench-frame-$n.jpg"
                docker compose exec -T worker ffmpeg -y -ss $t -i "$($clip.FilePath)" -frames:v 1 "/tmp/$tmpName" 2>$null | Out-Null
                docker compose cp "worker:/tmp/$tmpName" (Join-Path $runDir "frames\frame-$n.jpg") 2>$null | Out-Null
            }
            $framesNote = 'Frame stills from clip 1 (10% / 50% / 90%) are in the frames\ folder next to this report - check framing, captions and lip sync by eye; no automated figure replaces that.'
        } catch { $framesNote = 'Frame stills could not be extracted for this run (docker exec/cp failed); judge the clips in the app instead.' }
    }
    $clipReports += @{ Clip = $label; Checks = $checks }
}

# ---------------------------------------------------------------- report
$diskAfter = [math]::Round((Get-PSDrive $drive).Free / 1GB, 1)
$stats = Read-SampleStats -CsvPath $samplesCsv
$data = @{
    Title = "SnipNova benchmark - task $TaskId"
    GeneratedAt = (Get-Date -Format 'yyyy-MM-dd HH:mm') + ' (local)'
    EvidenceNote = 'USER-MACHINE RUN - numbers measured live on this machine by the benchmark pack. Stage timings are the app''s own records; utilisation was sampled during processing.'
    Specs = $specs
    Workload = @(
        'Fixture: JRE bake-off video https://www.youtube.com/watch?v=z6S9DyxVwLg (~14 min) or the synthetic fallback (FIXTURE.md)',
        'Niche: podcast_news (manual override)',
        'Target clips: 5; length Medium (30-60s); Music Off; Full Quality',
        "Task status at harvest: $($row.Status)"
    )
    CacheHit = $cacheHit
    StageRows = $stageRows
    SampleStats = $stats
    ClipReports = $clipReports
    FramesNote = $framesNote
    DiskBeforeGb = $diskBefore
    DiskAfterGb = $diskAfter
    RunDir = $runDir
}
$reportPath = Join-Path $runDir 'report.md'
[void](New-BenchmarkReport -Data $data -ReportPath $reportPath -BaselineDir $packDir -SetBaseline:$SetBaseline)
Write-Host ''
Write-Host "Report written: $reportPath" -ForegroundColor Green
Write-Host "Clips: $($clipRows.Count) | Total wall: $(if ($totalWall -ge 0) { '{0:N1}s' -f $totalWall } else { 'NOT OBSERVED' }) | Sampled: $($stats.Sampled)"
