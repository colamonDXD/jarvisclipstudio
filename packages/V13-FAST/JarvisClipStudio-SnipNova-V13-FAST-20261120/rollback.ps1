# SnipNova (JarvisClipStudio) - rollback (Consolidated v12.1 release, 2026-11-19)
#
# Puts back the install that install.ps1 backed up:
#   1. Stops the current stack.
#   2. Renames the current (new) project folder to
#      JarvisClipStudio-FAILED-<timestamp> - it is KEPT, never deleted.
#   3. Moves the backed-up project folder back to its original place.
#   4. Starts ONLY the database and restores the pre-install database dump
#      (the app database is dropped and recreated, then the dump is loaded).
#   5. Optionally (-RestoreVolumes, the nuclear option, OFF by default)
#      restores every Docker volume from the backup tar archives.
#   6. Starts the whole stack and runs the same smoke checks as the
#      installer (the LiteLLM gateway is checked from INSIDE the backend
#      container - its port is not published to the host).
#
# Every folder copy/move uses robocopy: this project tree contains
# frontend\node_modules paths longer than 260 characters and read-only
# files, which break plain PowerShell folder moves.
#
# -BackupRoot defaults to the newest JarvisClipStudio-BACKUP-* folder sitting
# next to the project folder.
# -SkipSmoke exists ONLY for rehearsing this script without a real stack.

[CmdletBinding()]
param(
    [string]$BackupRoot,
    [string]$ProjectDir = 'D:\EditedTool\JarvisClipStudio',
    [switch]$RestoreVolumes,
    [switch]$SkipSmoke
)

$ErrorActionPreference = 'Stop'

function Say($msg) { Write-Host ''; Write-Host "== $msg" }
function Info($msg) { Write-Host "   $msg" }
function Fail($msg) {
    Write-Host ''
    Write-Host "ROLLBACK STOPPED: $msg" -ForegroundColor Red
    exit 1
}

function Copy-FolderRobocopy {
    param([string]$From, [string]$To, [switch]$Move)
    if (-not (Test-Path -LiteralPath $From)) { Fail "Folder not found: $From" }
    if (-not (Test-Path -LiteralPath $To)) { New-Item -ItemType Directory -Path $To -Force | Out-Null }
    $rcArgs = @($From, $To, '/E', '/R:2', '/W:3', '/NP', '/NFL', '/NDL')
    if ($Move) { $rcArgs += '/MOVE' }
    & robocopy @rcArgs | Out-Host
    # robocopy exit codes 0-7 are all success flavours; 8+ is a real failure.
    if ($LASTEXITCODE -ge 8) { Fail "robocopy failed (exit code $LASTEXITCODE) copying $From to $To." }
}

function Get-EnvValue {
    param([string]$File, [string]$Name)
    if (-not (Test-Path $File)) { return $null }
    foreach ($line in Get-Content $File) {
        if ($line -match ("^\s*" + [regex]::Escape($Name) + "\s*=\s*(.+?)\s*$")) {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}
function Test-Http200 {
    param([string]$Url)
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}
function Wait-Http200 {
    param([string]$Url, [int]$TimeoutSec)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-Http200 -Url $Url) { return $true }
        Start-Sleep -Seconds 5
    }
    return $false
}

Say 'SnipNova (JarvisClipStudio) - Consolidated v12.1 release rollback'
$parent = Split-Path $ProjectDir -Parent
if (-not $BackupRoot) {
    $candidates = Get-ChildItem -Path $parent -Directory -Filter 'JarvisClipStudio-BACKUP-*' -ErrorAction SilentlyContinue | Sort-Object Name -Descending
    if (-not $candidates -or $candidates.Count -eq 0) { Fail "No JarvisClipStudio-BACKUP-* folder found next to $ProjectDir. Pass -BackupRoot explicitly." }
    $BackupRoot = $candidates[0].FullName
}
Info "Backup to restore: $BackupRoot"
Info "Project folder:    $ProjectDir"
$BackupProject = Join-Path $BackupRoot 'project'
$dumpPath = Join-Path (Join-Path $BackupRoot 'db') 'dump.sql'
if (-not (Test-Path $BackupProject)) { Fail "The backup has no project folder: $BackupProject" }
if (-not (Test-Path $dumpPath)) { Fail "The backup has no database dump: $dumpPath" }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail 'docker was not found on PATH. Start Docker Desktop, wait until it says it is running, and re-run.' }
if (-not (Get-Command robocopy -ErrorAction SilentlyContinue)) { Fail 'robocopy was not found on PATH. It is part of Windows; tell Sanji.' }

# ------------------------------------------------- Step 1: stop the stack
Say 'Step 1 of 6: stopping the current stack'
if (Test-Path (Join-Path $ProjectDir 'docker-compose.yml')) {
    Push-Location $ProjectDir
    try {
        & docker compose down
        if ($LASTEXITCODE -ne 0) { Fail "'docker compose down' failed in $ProjectDir. Stop the containers manually (Docker Desktop) and re-run." }
    } finally { Pop-Location }
} else {
    Info 'No current project folder with a compose file - skipping the stop (the install may have stopped before the new tree landed).'
}

# ------------------------------------- Step 2: keep the failed tree, restore
Say 'Step 2 of 6: putting the backed-up project folder back'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$failedDir = $null
if (Test-Path $ProjectDir) {
    $failedDir = Join-Path $parent "JarvisClipStudio-FAILED-$stamp"
    Copy-FolderRobocopy -From $ProjectDir -To $failedDir -Move
    Info "The current (new) tree was kept at: $failedDir"
}
Copy-FolderRobocopy -From $BackupProject -To $ProjectDir -Move
Info "Previous project folder restored to: $ProjectDir"

# --------------------------------------- Step 3: restore the database dump
Say 'Step 3 of 6: restoring the database from the pre-install dump'
Push-Location $ProjectDir
try {
    & docker compose up -d postgres
    if ($LASTEXITCODE -ne 0) { Fail 'Could not start the database container.' }
    $ready = $false
    for ($i = 0; $i -lt 24; $i++) {
        & docker compose exec -T postgres pg_isready -U supoclip -d supoclip | Out-Null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 5
    }
    if (-not $ready) { Fail 'The database did not become ready within 2 minutes.' }
    & docker compose cp $dumpPath 'postgres:/tmp/jcs-rollback-dump.sql'
    if ($LASTEXITCODE -ne 0) { Fail 'Could not copy the dump into the database container.' }
    Info 'Dropping and recreating the app database, then loading the dump...'
    & docker compose exec -T postgres psql -U supoclip -d postgres -v ON_ERROR_STOP=1 -c 'DROP DATABASE supoclip WITH (FORCE);'
    if ($LASTEXITCODE -ne 0) { Fail 'Could not drop the current app database. Nothing else was changed; tell Sanji.' }
    & docker compose exec -T postgres psql -U supoclip -d postgres -v ON_ERROR_STOP=1 -c 'CREATE DATABASE supoclip OWNER supoclip;'
    if ($LASTEXITCODE -ne 0) { Fail 'Could not recreate the app database. Tell Sanji - the dump file is still in the backup folder.' }
    & docker compose exec -T postgres psql -U supoclip -d supoclip -v ON_ERROR_STOP=1 -f /tmp/jcs-rollback-dump.sql
    if ($LASTEXITCODE -ne 0) { Fail 'Loading the dump failed. Tell Sanji - the dump file is still in the backup folder and the restored project folder is in place.' }
} finally { Pop-Location }
Info 'Database restored to its pre-install state.'

# ------------------------------------------ Step 4: volumes (only if asked)
if ($RestoreVolumes) {
    Say 'Step 4 of 6: restoring Docker volumes from the backup archives (nuclear option, as requested)'
    $volDir = Join-Path $BackupRoot 'volumes'
    $tars = Get-ChildItem -Path $volDir -Filter '*.tar.gz' -File -ErrorAction SilentlyContinue
    if (-not $tars -or $tars.Count -eq 0) { Fail "No volume archives found in $volDir." }
    foreach ($tarFile in $tars) {
        $vol = $tarFile.Name -replace '\.tar\.gz$', ''
        Info "Restoring volume $vol ..."
        & docker run --rm -v "${vol}:/data" -v "${volDir}:/backup" alpine sh -c "find /data -mindepth 1 -delete; tar xzf /backup/$($tarFile.Name) -C /data"
        if ($LASTEXITCODE -ne 0) { Fail "Volume restore failed for $vol. The project folder and database are already restored; tell Sanji before starting the stack." }
    }
} else {
    Say 'Step 4 of 6: volumes left as they are (pass -RestoreVolumes to also restore them from the backup archives)'
}

# ------------------------------------------------ Step 5: start the stack
Say 'Step 5 of 6: starting the restored stack'
Push-Location $ProjectDir
try {
    & docker compose up -d
    if ($LASTEXITCODE -ne 0) { Fail "'docker compose up -d' failed for the restored install. The restored files are in place; tell Sanji." }
} finally { Pop-Location }

# ---------------------------------------------------------- Step 6: smoke
if ($SkipSmoke) {
    Say 'Step 6 of 6: skipped (-SkipSmoke was given - rehearsal mode only)'
} else {
    Say 'Step 6 of 6: smoke checks'
    $envFile = Join-Path $ProjectDir '.env'
    $frontPort = Get-EnvValue -File $envFile -Name 'FRONTEND_PORT'
    if (-not $frontPort) { $frontPort = '3107' }
    $results = @()
    Info 'Waiting for the backend (http://localhost:8000/health, up to 3 minutes)...'
    $results += ,@('Backend health (http://localhost:8000/health)', (Wait-Http200 -Url 'http://localhost:8000/health' -TimeoutSec 180))
    Info "Waiting for the frontend (http://localhost:$frontPort, up to 3 minutes)..."
    $results += ,@("Frontend page (http://localhost:$frontPort)", (Wait-Http200 -Url "http://localhost:$frontPort" -TimeoutSec 180))
    Info 'Checking the LiteLLM gateway from inside the backend container...'
    Push-Location $ProjectDir
    try {
        $liteOut = & docker compose exec -T backend python -c "import os,urllib.request; req=urllib.request.Request('http://litellm:4000/v1/models', headers={'Authorization':'Bearer ' + os.environ.get('OPENAI_API_KEY','')}); print(urllib.request.urlopen(req, timeout=15).status)" 2>$null
        $okLite = ($LASTEXITCODE -eq 0 -and (($liteOut | Out-String) -match '200'))
    } catch { $okLite = $false } finally { Pop-Location }
    $results += ,@('LiteLLM gateway (checked from inside the backend container)', $okLite)
    Write-Host ''
    $anyFail = $false
    foreach ($r in $results) {
        if ($r[1]) { Write-Host "   PASS: $($r[0])" -ForegroundColor Green }
        else { Write-Host "   FAIL: $($r[0])" -ForegroundColor Red; $anyFail = $true }
    }
    if ($anyFail) {
        Write-Host ''
        Write-Host 'The restored install did not pass every smoke check. The files and database ARE restored; tell Sanji which check failed.'
        exit 1
    }
}

Say 'ROLLBACK COMPLETE'
Info 'Your previous install is back in place and running.'
if ($failedDir) { Info "The new (failed) tree was kept, untouched, at: $failedDir - delete it yourself once you are sure you do not need it." }
Info "The backup folder remains at: $BackupRoot (its project subfolder was moved out by this restore; the db and volumes archives are still inside)."
exit 0
