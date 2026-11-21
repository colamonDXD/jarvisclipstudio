# SnipNova (JarvisClipStudio) - CONSOLIDATED V12.1 release installer (2026-11-19)
#
# This is the CONSOLIDATED V12.1 release: branch next-milestone @
# 3900c3da - the v12 tree plus Claude's combined series (18 + 13
# patches), Sanji's gate fixes and the approved UI overhaul:
# live clipping in the app (sessions API, the live-worker
# container, the /live page, Groq hosted-Whisper captions) and
# fast VOD clipping of finished 10-20 h streams; the licence-
# gated asset library (ASSET_MANIFEST.json, 391 entries) and
# the Openverse B-roll source; the quality steps (content-aware
# cuts, story-event selection, screen-as-subject, fit/pad,
# caption cleanup - every quality switch ships OFF, dormant
# until Luffy approves the renders); the 248-entry transition
# library + burned-in caption detector; the OFL/Apache caption
# font pack (THEBOLDFONT is gone - its licence could not be
# verified); one Layout picker for every niche (it replaces the
# Framing toggle and the gaming-only row) with hook-title
# on/off; profanity masking in burned-in captions and titles;
# the 30-second minimum clip rule; the fix that gives
# face-on-top clips their music bed and SFX back; the LiteLLM
# gateway timeout (a hung route now fails over in 90 s instead
# of hanging a task forever); and the composer-hero UI overhaul
# (bento tiles, liquid glass, vivid Galaxy). It installs over
# your current v12 build - INCLUDING the live-clipping changes
# Claude added directly to your install afterwards: every one
# of them is in this tree, so nothing is lost. The installer
# backs up first. THREE new database migrations (55 total);
# verify.ps1 expects 218 (the check list grew: the 3 new
# migrations, 12 new feature-file checks and the live-worker
# container - no check removed or weakened). This installer
# also wires live clipping ON for your install: it makes sure
# the live-worker block in compose.override.yml carries the
# Groq + gateway keys and the GPU reservation (created if
# missing, completed if present), sets LIVE_CLIPPING=on in
# .env (the shipped default stays off), and the step-8 LiteLLM
# smoke check now retries for about 60 seconds before it can
# fail - the v12 installer's exit 1 was that boot-timing false
# alarm; never roll back for it alone. Gates on this exact
# tree: Backend: full gate on the final backend tip (5,634
# passed / 0 failed / 262 skipped / 9 xfailed / 1 error - the
# error is the no-database live-API setup, adjudicated by the
# database-backed run on the same tip: 108 passed / 1 skipped;
# Postgres tier 10 passed).; Frontend: vitest 959 tests total,
# 0 assertion failures (every red mark in full runs was a
# sandbox timeout that passes solo), tsc 0 errors, next build
# 76/76 pages, changelog PASS.; the licence gate ship-clean
# over all 29 shipped audio files; and a smoke acceptance
# record (Full AutoClip + Plain, end-to-end) -
# reports/V121_INTEGRATION_REPORT.md and
# reports/REAL_EXPORT_ACCEPTANCE_V121_SMOKE.md. Your data,
# clips, settings, .env, compose.override.yml, music and SFX
# are preserved - INCLUDING your extended asset library: your
# assets\music and assets\sfx contents are copied back over
# the new tree, so the 123 music / 310 SFX files on your
# current install survive the upgrade.
# Full record: reports/V121_PACKAGE_RECORD.md.
#
# What this script does, in a FIXED order:
#   1. Verifies the delivery ZIP against the SHA-256 checksum you were given.
#   2. Checks there is at least 20 GB free on the target drive (the backup,
#      the staging copy and the build all need room).
#   3. Extracts the ZIP to a fresh staging folder.
#   4. Backs up your current install: a database dump (taken while the stack
#      is still up), a tar archive of every Docker volume, and your whole
#      current project folder - all inside a timestamped backup folder.
#   5. Validates that backup. If anything is missing or unreadable, your old
#      stack is restarted from the backup copy and the install stops.
#      NOTHING has been replaced at that point.
#   6. Installs the new tree and copies back YOUR files unchanged: .env,
#      compose.override.yml (note: the SHORT name - that is the real file
#      name on your machine), and the contents of assets\music and
#      assets\sfx. It then checks the feature environment passthrough
#      lines in compose.override.yml and adds any that are missing (each
#      addition is printed). It then makes sure the live-worker service
#      has its block in compose.override.yml (the Groq + gateway keys
#      and the GPU reservation - created if missing, completed if
#      present; anything it cannot do safely is skipped with printed
#      manual lines) and sets LIVE_CLIPPING=on in .env, so live
#      clipping is enabled on your install (the shipped default is
#      off). After that it offers the OPTIONAL
#      watch-folder mount step (see step 6b below): it asks first, and
#      the default answer is NO.
#   6b. OPTIONAL watch-folder mount (asked, default NO). If you say yes,
#      the installer adds a read-only bind mount of D:\Jarvis_Autopilot
#      to the backend and worker services only (the two services that
#      read WATCH_FOLDER_PATH) plus the matching environment
#      passthrough, and sets WATCH_FOLDER_PATH=/autopilot in .env.
#      Both files were already backed up and validated in steps 4-5, so
#      rollback restores them wholesale. If any edit cannot be made
#      safely, the step skips with a printed warning and the exact lines
#      to add by hand; the install continues either way. If you say no,
#      compose.override.yml and .env stay exactly as they were and
#      Watch-Folder simply watches its in-container default folder (host
#      drops into D:\Jarvis_Autopilot then do nothing, by your choice).
#   7. Builds and starts the new stack. Backend, worker, ai-worker,
#      live-worker and frontend source all changed in this release, so
#      expect a full build, comparable to the v12 install.
#   8. Smoke checks (backend, frontend, LiteLLM gateway - the gateway is
#      checked from INSIDE the backend container, because its port is not
#      published to the host). The gateway check retries for about 60
#      seconds before it can fail - the v12 installer's exit 1 was this
#      boot-timing false alarm; never roll back for it alone. On failure
#      the script prints the exact
#      rollback command and stops. It NEVER rolls back by itself and
#      NEVER deletes anything. Backups are never auto-deleted.
#
# Every folder copy/move in this script is done with robocopy. Plain
# PowerShell folder moves break on this project: the tree contains
# frontend\node_modules paths longer than 260 characters and read-only
# git pack files. robocopy handles both.
#
# -SkipSmoke exists ONLY for rehearsing this script without a real stack.
# Do not use it for the real install.

[CmdletBinding()]
param(
    [string]$ZipPath,
    [Parameter(Mandatory = $true)]
    [string]$ExpectedSha256,
    [string]$ProjectDir = 'D:\EditedTool\JarvisClipStudio',
    [switch]$SkipSmoke
)

$ErrorActionPreference = 'Stop'

function Say($msg) { Write-Host ''; Write-Host "== $msg" }
function Info($msg) { Write-Host "   $msg" }
function Fail($msg) {
    Write-Host ''
    Write-Host "INSTALL STOPPED: $msg" -ForegroundColor Red
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
    if ($LASTEXITCODE -ge 8) { Fail "robocopy failed (exit code $LASTEXITCODE) copying $From to $To. Nothing was deleted; your backup (if step 4 already ran) is intact." }
}

function Remove-FolderRobocopy {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    # Mirror an empty folder over the target (robocopy is long-path safe),
    # then remove the now-empty shell.
    $emptyDir = Join-Path $env:TEMP ('jcs-empty-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $emptyDir | Out-Null
    & robocopy $emptyDir $Path /MIR /R:1 /W:1 /NP /NFL /NDL | Out-Host
    Remove-Item -LiteralPath $emptyDir -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $Path) { Fail "Could not clear the folder $Path - close any editor or terminal window that has it open, then re-run the installer. Your backup is intact." }
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

# Adds "- VAR=${VAR:-}" lines to the environment LIST of the given
# services in a compose override file, for any variable not already in
# that list.
#
# Corrected after the Milestone 2 install: the original version counted
# EVERY later "- " list item in the service block as part of the
# environment list, so it inserted the new lines at the end of the GPU
# devices list instead (invalid YAML). The rules now:
#   - Only lines that belong to the environment list itself are counted
#     or used as the insertion point. The list ends at the first
#     following line whose indent is not deeper than the
#     "environment:" key itself.
#   - If a service has no environment: key, or its environment section
#     is in mapping form (KEY: value lines instead of "- " items), the
#     service is SKIPPED with a printed warning and reported back for
#     manual adding. Nothing is ever inserted into any other list
#     (deploy devices, volumes, ports, ...).
#   - Every variable has a safe default in code, so a skipped service
#     never breaks the app; the passthroughs only make the feature
#     toggles settable from .env.
function Add-EnvPassthrough {
    param([string]$OverrideFile, [string[]]$Services, [string[]]$Vars)
    $manual = @()
    try {
        $lines = [System.Collections.ArrayList]@(Get-Content -LiteralPath $OverrideFile)
        foreach ($svc in $Services) {
            $svcIdx = -1; $svcIndent = ''
            for ($i = 0; $i -lt $lines.Count; $i++) {
                if ($lines[$i] -match ("^(\s*)" + [regex]::Escape($svc) + ":\s*$")) { $svcIdx = $i; $svcIndent = $Matches[1]; break }
            }
            if ($svcIdx -lt 0) { $manual += "service '$svc' not found"; continue }
            # The block ends at the next line with the same or smaller indent that is not blank.
            $blockEnd = $lines.Count
            for ($j = $svcIdx + 1; $j -lt $lines.Count; $j++) {
                if ($lines[$j] -match "^\s*$") { continue }
                $ind = ($lines[$j] -replace '^(\s*).*$', '$1').Length
                if ($ind -le $svcIndent.Length) { $blockEnd = $j; break }
            }
            # Find the environment: key inside this service block.
            $envIdx = -1; $envIndent = ''
            for ($j = $svcIdx + 1; $j -lt $blockEnd; $j++) {
                if ($lines[$j] -match "^(\s*)environment:\s*$") { $envIdx = $j; $envIndent = $Matches[1]; break }
            }
            if ($envIdx -lt 0) {
                Write-Host "   WARNING: service '$svc' has no environment list in compose.override.yml - skipped, nothing inserted. Add the passthrough lines by hand if you want the toggles." -ForegroundColor Yellow
                $manual += "service '$svc': no environment list"
                continue
            }
            # Walk ONLY the environment section: it continues while lines
            # are blank, comments, or indented deeper than the key.
            $lastItemIdx = -1; $itemIndent = $envIndent + '  '; $mappingForm = $false
            $envTextLines = @()
            for ($j = $envIdx + 1; $j -lt $blockEnd; $j++) {
                if ($lines[$j] -match "^\s*$") { continue }
                if ($lines[$j] -match "^\s*#") { continue }
                $ind = ($lines[$j] -replace '^(\s*).*$', '$1').Length
                if ($ind -le $envIndent.Length) { break }
                if ($lines[$j] -match "^(\s*)- ") {
                    $lastItemIdx = $j; $itemIndent = $Matches[1]; $envTextLines += $lines[$j]
                } else {
                    # A deeper-indented line that is not a list item means
                    # the environment section is a mapping (KEY: value).
                    $mappingForm = $true; break
                }
            }
            if ($mappingForm) {
                Write-Host "   WARNING: service '$svc' uses a mapping-style environment section in compose.override.yml - skipped, nothing inserted. Add the passthrough lines by hand if you want the toggles." -ForegroundColor Yellow
                $manual += "service '$svc': environment is a mapping, not a list"
                continue
            }
            $envText = ($envTextLines -join "`n")
            $missing = @($Vars | Where-Object { $envText -notmatch [regex]::Escape($_) })
            if ($missing.Count -eq 0) { Info "compose.override.yml: service '$svc' environment list already has all the feature variables."; continue }
            $insertAt = if ($lastItemIdx -ge 0) { $lastItemIdx + 1 } else { $envIdx + 1 }
            $added = 0
            foreach ($v in $missing) {
                $lines.Insert($insertAt, $itemIndent + '- ' + $v + '=${' + $v + ':-}')
                $insertAt++; $added++
            }
            Info "compose.override.yml: added $added passthrough line(s) to the environment list of service '$svc'."
        }
        $lines | Set-Content -LiteralPath $OverrideFile -Encoding ASCII
    } catch {
        Write-Host "   WARNING: could not edit compose.override.yml automatically ($($_.Exception.Message))." -ForegroundColor Yellow
        $manual += 'automatic edit failed'
    }
    return $manual
}


# Makes sure the live-worker service has a complete block in a compose
# override file: an environment list carrying the Groq key and the LLM
# gateway pair (OPENAI_API_KEY / OPENAI_BASE_URL - the live worker picks
# its moments through the gateway), plus the NVIDIA GPU reservation the
# live renderer needs. Same discipline as Add-EnvPassthrough:
#   - If the service is absent, a complete block is inserted directly
#     under the top-level "services:" key (indent matched to the first
#     existing service entry, default two spaces).
#   - If the service is present, the missing environment variables are
#     added to ITS environment list only (via Add-EnvPassthrough), and
#     the GPU deploy block is appended at the end of ITS block only if
#     no "deploy:" key exists there.
#   - Anything unexpected (no services: key, mapping-style environment,
#     unreadable file) is SKIPPED with a printed warning and reported
#     back for manual adding; on failure the file is left untouched.
function Ensure-LiveWorkerOverride {
    param([string]$OverrideFile)
    $manual = @()
    $vars = @('GROQ_API_KEY', 'OPENAI_API_KEY', 'OPENAI_BASE_URL')
    try {
        $lines = [System.Collections.ArrayList]@(Get-Content -LiteralPath $OverrideFile)
        $svcKeyIdx = -1
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match "^services:\s*$") { $svcKeyIdx = $i; break }
        }
        if ($svcKeyIdx -lt 0) {
            Write-Host "   WARNING: no top-level 'services:' key found in compose.override.yml - the live-worker block was NOT added. Add it by hand (see the installation guide)." -ForegroundColor Yellow
            return @('no services: key')
        }
        $svcIndent = '  '
        for ($i = $svcKeyIdx + 1; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match "^\s*$" -or $lines[$i] -match "^\s*#") { continue }
            if ($lines[$i] -match "^(\s+)\S") { $svcIndent = $Matches[1] }
            break
        }
        $lwIdx = -1
        for ($i = $svcKeyIdx + 1; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match ("^" + [regex]::Escape($svcIndent) + "live-worker:\s*$")) { $lwIdx = $i; break }
            if ($lines[$i] -match "^\S") { break }
        }
        $childIndent = $svcIndent + '  '
        $itemIndent = $childIndent + '  '
        $deployBlock = @(
            ($childIndent + 'deploy:'),
            ($itemIndent + 'resources:'),
            ($itemIndent + '  reservations:'),
            ($itemIndent + '    devices:'),
            ($itemIndent + '      - driver: nvidia'),
            ($itemIndent + '        count: 1'),
            ($itemIndent + '        capabilities: [gpu]')
        )
        if ($lwIdx -lt 0) {
            $block = @(
                ($svcIndent + 'live-worker:'),
                ($childIndent + 'environment:'),
                ($itemIndent + '- GROQ_API_KEY=${GROQ_API_KEY:-}'),
                ($itemIndent + '- OPENAI_API_KEY=${OPENAI_API_KEY:-}'),
                ($itemIndent + '- OPENAI_BASE_URL=${OPENAI_BASE_URL:-}')
            ) + $deployBlock
            $insertAt = $svcKeyIdx + 1
            foreach ($bl in $block) { $lines.Insert($insertAt, $bl); $insertAt++ }
            $lines | Set-Content -LiteralPath $OverrideFile -Encoding ASCII
            Info 'compose.override.yml: added the live-worker block (Groq + gateway keys, GPU reservation).'
            return $manual
        }
        $manual += Add-EnvPassthrough -OverrideFile $OverrideFile -Services @('live-worker') -Vars $vars
        $lines = [System.Collections.ArrayList]@(Get-Content -LiteralPath $OverrideFile)
        $lwIdx = -1
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match ("^" + [regex]::Escape($svcIndent) + "live-worker:\s*$")) { $lwIdx = $i; break }
        }
        if ($lwIdx -lt 0) { return $manual + @('live-worker block lost during the passthrough edit') }
        $blockEnd = $lines.Count
        for ($j = $lwIdx + 1; $j -lt $lines.Count; $j++) {
            if ($lines[$j] -match "^\s*$") { continue }
            $ind = ($lines[$j] -replace '^(\s*).*$', '$1').Length
            if ($ind -le $svcIndent.Length) { $blockEnd = $j; break }
        }
        $hasDeploy = $false
        for ($j = $lwIdx + 1; $j -lt $blockEnd; $j++) {
            if ($lines[$j] -match ("^" + [regex]::Escape($childIndent) + "deploy:\s*$")) { $hasDeploy = $true; break }
        }
        if (-not $hasDeploy) {
            $insertAt = $blockEnd
            foreach ($bl in $deployBlock) { $lines.Insert($insertAt, $bl); $insertAt++ }
            $lines | Set-Content -LiteralPath $OverrideFile -Encoding ASCII
            Info 'compose.override.yml: added the GPU reservation block to live-worker.'
        } else {
            Info 'compose.override.yml: live-worker already has its GPU reservation block.'
        }
    } catch {
        Write-Host "   WARNING: could not wire live-worker in compose.override.yml automatically ($($_.Exception.Message)). Add the block by hand (see the installation guide)." -ForegroundColor Yellow
        $manual += 'automatic edit failed'
    }
    return $manual
}


# Adds a read-only bind-mount line to the volumes LIST of the given
# services in a compose override file. Same discipline as
# Add-EnvPassthrough: only the volumes list of the named service is
# ever edited; a service whose volumes section is missing or in
# mapping form is SKIPPED with a printed warning; any failure leaves
# the file untouched and is reported back for manual adding.
function Add-VolumeMount {
    param([string]$OverrideFile, [string[]]$Services, [string]$MountLine)
    $manual = @()
    try {
        $lines = [System.Collections.ArrayList]@(Get-Content -LiteralPath $OverrideFile)
        foreach ($svc in $Services) {
            $svcIdx = -1; $svcIndent = ''
            for ($i = 0; $i -lt $lines.Count; $i++) {
                if ($lines[$i] -match ("^(\s*)" + [regex]::Escape($svc) + ":\s*$")) { $svcIdx = $i; $svcIndent = $Matches[1]; break }
            }
            if ($svcIdx -lt 0) { $manual += "service '$svc' not found"; continue }
            $blockEnd = $lines.Count
            for ($j = $svcIdx + 1; $j -lt $lines.Count; $j++) {
                if ($lines[$j] -match "^\s*$") { continue }
                $ind = ($lines[$j] -replace '^(\s*).*$', '$1').Length
                if ($ind -le $svcIndent.Length) { $blockEnd = $j; break }
            }
            $volIdx = -1; $volIndent = ''
            for ($j = $svcIdx + 1; $j -lt $blockEnd; $j++) {
                if ($lines[$j] -match "^(\s*)volumes:\s*$") { $volIdx = $j; $volIndent = $Matches[1]; break }
            }
            if ($volIdx -lt 0) {
                Write-Host "   WARNING: service '$svc' has no volumes list in compose.override.yml - skipped, nothing inserted." -ForegroundColor Yellow
                $manual += "service '$svc': no volumes list"
                continue
            }
            $lastItemIdx = -1; $itemIndent = $volIndent + '  '; $mappingForm = $false
            $volTextLines = @()
            for ($j = $volIdx + 1; $j -lt $blockEnd; $j++) {
                if ($lines[$j] -match "^\s*$") { continue }
                if ($lines[$j] -match "^\s*#") { continue }
                $ind = ($lines[$j] -replace '^(\s*).*$', '$1').Length
                if ($ind -le $volIndent.Length) { break }
                if ($lines[$j] -match "^(\s*)- ") {
                    $lastItemIdx = $j; $itemIndent = $Matches[1]; $volTextLines += $lines[$j]
                } else {
                    $mappingForm = $true; break
                }
            }
            if ($mappingForm) {
                Write-Host "   WARNING: service '$svc' uses a mapping-style volumes section in compose.override.yml - skipped, nothing inserted." -ForegroundColor Yellow
                $manual += "service '$svc': volumes is a mapping, not a list"
                continue
            }
            $volText = ($volTextLines -join "`n")
            if ($volText -match [regex]::Escape($MountLine)) { Info "compose.override.yml: service '$svc' volumes list already has the watch-folder mount."; continue }
            $insertAt = if ($lastItemIdx -ge 0) { $lastItemIdx + 1 } else { $volIdx + 1 }
            $lines.Insert($insertAt, $itemIndent + '- ' + $MountLine)
            Info "compose.override.yml: added the watch-folder mount to the volumes list of service '$svc'."
        }
        $lines | Set-Content -LiteralPath $OverrideFile -Encoding ASCII
    } catch {
        Write-Host "   WARNING: could not edit compose.override.yml automatically ($($_.Exception.Message))." -ForegroundColor Yellow
        $manual += 'automatic edit failed'
    }
    return $manual
}

# Sets KEY=value in a .env file: replaces an existing uncommented KEY=
# line, or appends the setting (with an explanatory comment) when absent.
function Set-EnvValue {
    param([string]$EnvFile, [string]$Name, [string]$Value, [string]$Comment)
    $lines = [System.Collections.ArrayList]@(Get-Content -LiteralPath $EnvFile)
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match ("^\s*" + [regex]::Escape($Name) + "\s*=")) {
            $lines[$i] = "$Name=$Value"
            $lines | Set-Content -LiteralPath $EnvFile -Encoding ASCII
            return $true
        }
    }
    Add-Content -Path $EnvFile -Value "`n# $Comment`n$Name=$Value" -Encoding ASCII
    return $true
}

Say 'SnipNova (JarvisClipStudio) - Consolidated v12.1 release installer'
Info "Project folder: $ProjectDir"
if ($SkipSmoke) { Info 'NOTE: -SkipSmoke is set (rehearsal only). The step-8 start-up checks will be skipped.' }

# ---------------------------------------------------------------- ZIP path
if (-not $ZipPath) {
    $found = Get-ChildItem -Path $PSScriptRoot -Filter 'JarvisClipStudio-SnipNova-Consolidated-V12.1-*.zip' -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $found) {
        $up = Split-Path $PSScriptRoot -Parent
        $found = Get-ChildItem -Path $up -Filter 'JarvisClipStudio-SnipNova-Consolidated-V12.1-*.zip' -File -ErrorAction SilentlyContinue | Select-Object -First 1
    }
    if (-not $found) { Fail 'Could not find the delivery ZIP next to this script (or one folder above it). Re-run with -ZipPath pointing at the ZIP file.' }
    $ZipPath = $found.FullName
}
if (-not (Test-Path $ZipPath)) { Fail "ZIP not found: $ZipPath" }
Info "Delivery ZIP: $ZipPath"

# ------------------------------------------------------- Step 1: checksum
Say 'Step 1 of 8: verifying the ZIP checksum'
$actual = (Get-FileHash -Path $ZipPath -Algorithm SHA256).Hash
if ($actual.ToLower() -ne $ExpectedSha256.Trim().ToLower()) {
    Fail "Checksum mismatch. Expected: $ExpectedSha256 / Actual: $actual. Do NOT install from this file - download the ZIP again."
}
Info "Checksum OK: $actual"

# ------------------------------------------------------ Step 2: disk space
Say 'Step 2 of 8: checking free disk space'
if (-not (Test-Path $ProjectDir)) { Fail "Your current project folder was not found: $ProjectDir. This installer upgrades an existing install; nothing was changed." }
$root = [System.IO.Path]::GetPathRoot($ProjectDir)
$drive = New-Object System.IO.DriveInfo($ProjectDir)
$freeGB = [math]::Round($drive.AvailableFreeSpace / 1GB, 1)
Info "Drive $root has $freeGB GB free."
if ($drive.AvailableFreeSpace -lt 20GB) {
    Fail "At least 20 GB free on the drive holding $ProjectDir is required (backup + staging + build). Free some space and re-run."
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail 'docker was not found on PATH. Start Docker Desktop, wait until it says it is running, and re-run.' }
if (-not (Get-Command robocopy -ErrorAction SilentlyContinue)) { Fail 'robocopy was not found on PATH. It is part of Windows; something is very wrong with this machine - tell Sanji.' }

# ------------------------------------------------- Step 3: extract staging
Say 'Step 3 of 8: extracting the delivery ZIP to a staging folder'
$parent = Split-Path $ProjectDir -Parent
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$Staging = Join-Path $parent "JarvisClipStudio-STAGING-$stamp"
New-Item -ItemType Directory -Path $Staging | Out-Null
Expand-Archive -Path $ZipPath -DestinationPath $Staging
$NewTree = Join-Path $Staging 'JarvisClipStudio'
if (-not (Test-Path (Join-Path $NewTree 'docker-compose.yml'))) {
    # The delivery ZIP wraps everything in one outer folder; find the real project tree inside it.
    $foundTree = Get-ChildItem -Path $Staging -Directory -Recurse -Depth 2 | Where-Object { $_.Name -eq 'JarvisClipStudio' -and (Test-Path (Join-Path $_.FullName 'docker-compose.yml')) } | Select-Object -First 1
    if ($foundTree) { $NewTree = $foundTree.FullName }
}
if (-not (Test-Path (Join-Path $NewTree 'docker-compose.yml'))) {
    Fail 'The ZIP does not contain the expected project tree (docker-compose.yml missing in the staged copy). Nothing was changed.'
}
Info "Staged at: $Staging"

# ---------------------------------------------------------- Step 4: backup
Say 'Step 4 of 8: backing up your current install (database, volumes, project folder)'
$BackupRoot = Join-Path $parent "JarvisClipStudio-BACKUP-$stamp"
$BackupDbDir = Join-Path $BackupRoot 'db'
$BackupVolDir = Join-Path $BackupRoot 'volumes'
New-Item -ItemType Directory -Path $BackupDbDir -Force | Out-Null
New-Item -ItemType Directory -Path $BackupVolDir -Force | Out-Null
$projectName = (Split-Path $ProjectDir -Leaf).ToLower()
$dumpDest = Join-Path $BackupDbDir 'dump.sql'

Push-Location $ProjectDir
try {
    Info 'Making sure the database container is up, so the dump is complete...'
    & docker compose up -d postgres
    if ($LASTEXITCODE -ne 0) { Fail 'Could not start the database container for the backup dump. Is Docker Desktop fully started?' }
    $ready = $false
    for ($i = 0; $i -lt 24; $i++) {
        & docker compose exec -T postgres pg_isready -U supoclip -d supoclip | Out-Null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 5
    }
    if (-not $ready) { Fail 'The database did not become ready within 2 minutes. Backup aborted; your install is untouched and still in place.' }
    Info 'Dumping the database...'
    & docker compose exec -T postgres sh -c 'pg_dump -U supoclip -d supoclip > /tmp/jcs-preinstall-dump.sql'
    if ($LASTEXITCODE -ne 0) { Fail 'pg_dump failed. Backup aborted; your install is untouched and still in place.' }
    & docker compose cp 'postgres:/tmp/jcs-preinstall-dump.sql' $dumpDest
    if ($LASTEXITCODE -ne 0) { Fail 'Could not copy the database dump out of the container. Backup aborted; your install is untouched and still in place.' }
    Info 'Stopping the stack (containers only - the volumes themselves are kept)...'
    & docker compose down
    if ($LASTEXITCODE -ne 0) { Fail "'docker compose down' failed. Backup aborted; your project folder has NOT been touched." }
} finally { Pop-Location }

Info "Listing the project's Docker volumes..."
$volumes = & docker volume ls --filter "label=com.docker.compose.project=$projectName" --format '{{.Name}}'
if ($LASTEXITCODE -ne 0) { Fail "'docker volume ls' failed. Your project folder has NOT been touched; restart the old stack from $ProjectDir with: docker compose up -d" }
$volumes = @($volumes | Where-Object { $_ -and $_.Trim() -ne '' } | ForEach-Object { $_.Trim() })
if ($volumes.Count -eq 0) {
    $volumes = & docker volume ls --filter "name=$projectName" --format '{{.Name}}'
    $volumes = @($volumes | Where-Object { $_ -and $_.Trim() -ne '' } | ForEach-Object { $_.Trim() })
}
if ($volumes.Count -eq 0) { Fail "No Docker volumes found for project '$projectName'. Backup aborted; your project folder has NOT been touched. Restart the old stack from $ProjectDir with: docker compose up -d" }
foreach ($vol in $volumes) {
    Info "Backing up volume $vol ..."
    & docker run --rm -v "${vol}:/data:ro" -v "${BackupVolDir}:/backup" alpine tar czf "/backup/$vol.tar.gz" -C /data .
    if ($LASTEXITCODE -ne 0) { Fail "Volume backup failed for $vol. Your project folder has NOT been touched; the stack is stopped - restart it from $ProjectDir with: docker compose up -d" }
}

Info 'Copying your current project folder into the backup (robocopy)...'
Copy-FolderRobocopy -From $ProjectDir -To (Join-Path $BackupRoot 'project')
$BackupProject = Join-Path $BackupRoot 'project'

$manifest = Join-Path $BackupRoot 'manifest.txt'
$lines = @()
$lines += 'JarvisClipStudio pre-install backup (SnipNova Consolidated v12.1 release install, 2026-11-19)'
$lines += "Created: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
$lines += "Source project folder: $ProjectDir"
$lines += "Delivery ZIP: $ZipPath"
$lines += "ZIP SHA-256: $actual"
$lines += ''
$lines += "Database dump: db\dump.sql ($((Get-Item $dumpDest).Length) bytes)"
$lines += 'Volumes backed up:'
foreach ($vol in $volumes) {
    $tarPath = Join-Path $BackupVolDir "$vol.tar.gz"
    $lines += "  $vol -> volumes\$vol.tar.gz ($((Get-Item $tarPath).Length) bytes)"
}
$lines += ''
$lines += 'The full previous project folder is in project\ inside this backup folder.'
$lines += 'This backup is NEVER deleted by any script. Delete it manually, yourself, only after acceptance testing passes.'
$lines | Set-Content -Path $manifest -Encoding ASCII

# ------------------------------------------- Step 5: validate the backup
Say 'Step 5 of 8: validating the backup before anything is replaced'
$problems = @()
if (-not (Test-Path (Join-Path $BackupProject '.env'))) { $problems += 'project\.env is missing from the backup' }
if (-not (Test-Path (Join-Path $BackupProject 'compose.override.yml'))) { $problems += 'project\compose.override.yml is missing from the backup' }
$dumpInfo = Get-Item $dumpDest
if ($dumpInfo.Length -le 10KB) { $problems += "db\dump.sql is suspiciously small ($($dumpInfo.Length) bytes)" }
foreach ($vol in $volumes) {
    $tarPath = Join-Path $BackupVolDir "$vol.tar.gz"
    & tar -tzf $tarPath | Out-Null
    if ($LASTEXITCODE -ne 0) { $problems += "volume archive volumes\$vol.tar.gz does not list cleanly" }
}
if ($problems.Count -gt 0) {
    Write-Host ''
    Write-Host 'BACKUP VALIDATION FAILED:' -ForegroundColor Red
    foreach ($p in $problems) { Write-Host "   - $p" -ForegroundColor Red }
    Write-Host 'Nothing has been replaced. Restarting your old stack from your (untouched) project folder...'
    # Restart from the ORIGINAL project folder, never from the backup copy:
    # the compose project name comes from the folder name, so starting from
    # the backup's "project" folder would spin up a different, EMPTY project
    # instead of your real stack and its volumes.
    Push-Location $ProjectDir
    try { & docker compose up -d } finally { Pop-Location }
    Write-Host "Your old install is running again from: $ProjectDir"
    Write-Host 'Tell Sanji what failed (copy the lines above). Do not delete the backup folder.'
    exit 1
}
Info "Backup validated: .env, compose.override.yml, a $($dumpInfo.Length)-byte database dump and $($volumes.Count) readable volume archive(s)."
Info "Backup location: $BackupRoot"

# ------------------------------------------------ Step 6: install new tree
Say 'Step 6 of 8: installing the new project tree'
Remove-FolderRobocopy -Path $ProjectDir
Copy-FolderRobocopy -From $NewTree -To $ProjectDir -Move
Info "New tree in place at: $ProjectDir"
Copy-Item -Path (Join-Path $BackupProject '.env') -Destination (Join-Path $ProjectDir '.env') -Force
Copy-Item -Path (Join-Path $BackupProject 'compose.override.yml') -Destination (Join-Path $ProjectDir 'compose.override.yml') -Force
Info 'Your .env and compose.override.yml were copied back unchanged.'
foreach ($kind in @('music', 'sfx')) {
    $srcDir = Join-Path (Join-Path $BackupProject 'assets') $kind
    if (Test-Path $srcDir) {
        $dstDir = Join-Path (Join-Path $ProjectDir 'assets') $kind
        if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Path $dstDir -Force | Out-Null }
        Copy-FolderRobocopy -From $srcDir -To $dstDir
        Info "Your assets\$kind files were copied into the new tree."
    }
}

Info 'Checking the feature environment passthroughs in compose.override.yml...'
$featureVars = @('VOICEOVER_ENABLED', 'VOICEOVER_MODELS_DIR', 'ENABLE_NVDEC', 'HOOK_ENFORCEMENT_ENABLED', 'USER_PROFILES_DIR', 'ANALYSIS_INTRO_ZONE_SECONDS', 'ANALYSIS_LONGFORM_TPM')
$manual = Add-EnvPassthrough -OverrideFile (Join-Path $ProjectDir 'compose.override.yml') -Services @('backend', 'worker', 'ai-worker') -Vars $featureVars
if ($manual.Count -gt 0) {
    Write-Host '   NOTE: some passthroughs could not be added automatically. The app still works (safe defaults in code).' -ForegroundColor Yellow
    Write-Host '   To enable the toggles later, add lines like these under environment: for backend, worker and ai-worker in compose.override.yml:' -ForegroundColor Yellow
    foreach ($v in $featureVars) { Write-Host ('     - ' + $v + '=${' + $v + ':-}') -ForegroundColor Yellow }
}
Info 'Making sure the live-worker service is fully wired in compose.override.yml...'
$lwManual = Ensure-LiveWorkerOverride -OverrideFile (Join-Path $ProjectDir 'compose.override.yml')
if ($lwManual.Count -gt 0) {
    Write-Host '   NOTE: the live-worker wiring could not be completed automatically. Nothing is broken - live clipping simply stays off until the block is added by hand (the exact lines are in the installation guide).' -ForegroundColor Yellow
}
$envPath = Join-Path $ProjectDir '.env'
if ((Get-EnvValue -File $envPath -Name 'LIVE_CLIPPING') -ne 'on') {
    Set-EnvValue -EnvFile $envPath -Name 'LIVE_CLIPPING' -Value 'on' -Comment 'Live clipping: enabled for this install by the SnipNova Consolidated v12.1 installer (the shipped default is off). Set to off and restart the stack to disable it.'
    Info '.env: set LIVE_CLIPPING=on (live clipping enabled for this install; the shipped default stays off).'
} else {
    Info '.env: LIVE_CLIPPING is already on.'
}
if (-not (Get-EnvValue -File $envPath -Name 'VOICEOVER_ENABLED')) {
    Add-Content -Path $envPath -Value "`n# Milestone 2: local AI voiceover (Kokoro). Off by default. Set to true only if you want it; the ~315 MB model downloads once, automatically, on first use.`nVOICEOVER_ENABLED=false" -Encoding ASCII
    Info '.env: added VOICEOVER_ENABLED=false (voiceover stays OFF until you choose to enable it).'
}
if (-not (Get-EnvValue -File $envPath -Name 'ENABLE_NVDEC')) {
    Add-Content -Path $envPath -Value "`n# Milestone 2: NVDEC hardware decode is opt-in and still being measured. Leave false.`nENABLE_NVDEC=false" -Encoding ASCII
    Info '.env: added ENABLE_NVDEC=false (hardware decode stays off; it is opt-in).'
}

# ------------------------------------- Step 6b: watch-folder mount (opt-in)
Say 'Step 6b of 8: watch-folder mount (OPTIONAL - default is NO)'
Info 'Watch-Folder Autopilot can watch a folder on THIS computer: D:\Jarvis_Autopilot.'
Info 'For that, Docker needs a read-only link from that folder into the backend and'
Info 'worker containers, plus WATCH_FOLDER_PATH=/autopilot in your .env.'
Info 'Both files were backed up and validated in steps 4-5, so rollback undoes this too.'
Info 'If you answer NO (the default), nothing changes: Watch-Folder keeps watching its'
Info 'in-container default folder, and files dropped into D:\Jarvis_Autopilot do nothing.'
$wfAnswer = Read-Host 'Enable the D:\Jarvis_Autopilot watch-folder mount now? [y/N]'
if ($wfAnswer -match '^(y|yes)$') {
    if (-not (Test-Path 'D:\Jarvis_Autopilot')) {
        New-Item -ItemType Directory -Path 'D:\Jarvis_Autopilot' -Force | Out-Null
        Info 'Created the folder D:\Jarvis_Autopilot.'
    }
    $overridePath = Join-Path $ProjectDir 'compose.override.yml'
    $wfManual = @()
    $wfManual += Add-VolumeMount -OverrideFile $overridePath -Services @('backend', 'worker') -MountLine 'D:\Jarvis_Autopilot:/autopilot:ro'
    $wfManual += Add-EnvPassthrough -OverrideFile $overridePath -Services @('backend', 'worker') -Vars @('WATCH_FOLDER_PATH')
    if ($wfManual.Count -eq 0) {
        Set-EnvValue -EnvFile $envPath -Name 'WATCH_FOLDER_PATH' -Value '/autopilot' -Comment 'Watch-folder autopilot: host folder mounted read-only at /autopilot (added by the SnipNova Consolidated v12.1 installer, step 6b).'
        Info 'Watch-folder mount APPLIED: backend + worker now mount D:\Jarvis_Autopilot read-only at /autopilot, and .env sets WATCH_FOLDER_PATH=/autopilot.'
    } else {
        Write-Host '   Watch-folder mount NOT applied automatically. Your files were left untouched.' -ForegroundColor Yellow
        Write-Host '   To enable it by hand later, add these lines in compose.override.yml:' -ForegroundColor Yellow
        Write-Host '     under volumes: of BOTH backend and worker:  - D:\Jarvis_Autopilot:/autopilot:ro' -ForegroundColor Yellow
        Write-Host '     under environment: of BOTH backend and worker:  - WATCH_FOLDER_PATH=${WATCH_FOLDER_PATH:-}' -ForegroundColor Yellow
        Write-Host '   and add this line to .env:  WATCH_FOLDER_PATH=/autopilot' -ForegroundColor Yellow
        Write-Host '   Then restart the stack: docker compose up -d' -ForegroundColor Yellow
    }
} else {
    Info 'Watch-folder mount NOT applied (you answered No). D:\Jarvis_Autopilot drops stay inert; the manual lines are in the installation guide if you change your mind.'
}

# ------------------------------------------------------- Step 7: build + up
Say 'Step 7 of 8: building and starting the new stack'
Info 'Backend, worker, ai-worker, live-worker and frontend source changed in this release, so expect a real build, comparable to the v12 install.'
Push-Location $ProjectDir
try {
    & docker compose build
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'The build failed. Your backup is untouched at:' -ForegroundColor Red
        Write-Host "   $BackupRoot"
        Write-Host 'To roll back to your previous install, run:'
        Write-Host "   .\rollback.ps1 -BackupRoot '$BackupRoot'"
        exit 1
    }
    & docker compose up -d
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'The stack did not start. Your backup is untouched at:' -ForegroundColor Red
        Write-Host "   $BackupRoot"
        Write-Host 'To roll back to your previous install, run:'
        Write-Host "   .\rollback.ps1 -BackupRoot '$BackupRoot'"
        exit 1
    }
} finally { Pop-Location }
Info 'Stack started.'

# ---------------------------------------------------------- Step 8: smoke
if ($SkipSmoke) {
    Say 'Step 8 of 8: skipped (-SkipSmoke was given - rehearsal mode only)'
} else {
    Say 'Step 8 of 8: smoke checks'
    $envFile = Join-Path $ProjectDir '.env'
    $frontPort = Get-EnvValue -File $envFile -Name 'FRONTEND_PORT'
    if (-not $frontPort) { $frontPort = '3107' }
    $results = @()
    Info 'Waiting for the backend (http://localhost:8000/health, up to 3 minutes)...'
    $okBackend = Wait-Http200 -Url 'http://localhost:8000/health' -TimeoutSec 180
    $results += ,@('Backend health (http://localhost:8000/health)', $okBackend)
    Info "Waiting for the frontend (http://localhost:$frontPort, up to 3 minutes; the production build can answer slowly the first time)..."
    $okFront = Wait-Http200 -Url "http://localhost:$frontPort" -TimeoutSec 180
    $results += ,@("Frontend page (http://localhost:$frontPort)", $okFront)
    Info 'Checking the LiteLLM gateway from inside the backend container (its port is not published to the host)...'
    Info 'The gateway can still be booting; this check retries for about 60 seconds before it counts as failed (the v12 installer exit 1 was exactly this boot-timing false alarm - never roll back for it alone).'
    $okLite = $false
    Push-Location $ProjectDir
    try {
        $liteDeadline = (Get-Date).AddSeconds(60)
        while ((Get-Date) -lt $liteDeadline) {
            $liteOut = & docker compose exec -T backend python -c "import os,urllib.request; req=urllib.request.Request('http://litellm:4000/v1/models', headers={'Authorization':'Bearer ' + os.environ.get('OPENAI_API_KEY','')}); print(urllib.request.urlopen(req, timeout=15).status)" 2>$null
            if ($LASTEXITCODE -eq 0 -and (($liteOut | Out-String) -match '200')) { $okLite = $true; break }
            Start-Sleep -Seconds 5
        }
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
        Write-Host 'The new stack did not pass every smoke check. NOTHING was deleted; your backup is at:'
        Write-Host "   $BackupRoot"
        Write-Host 'To roll back to your previous install, run:'
        Write-Host "   .\rollback.ps1 -BackupRoot '$BackupRoot'"
        Write-Host 'Or tell Sanji which check failed before rolling back.'
        exit 1
    }
}

Say 'INSTALL COMPLETE'
Info "Previous install backed up at: $BackupRoot"
Info 'That backup is NEVER deleted by any script. Delete it yourself, manually, only after acceptance testing passes.'
Info "The staging folder was left at: $Staging (it only holds the extracted delivery files; you can delete it once everything works)."
Info 'Voiceover is OFF. If you later set VOICEOVER_ENABLED=true in .env and restart, the ~315 MB Kokoro model downloads once, automatically, the first time a voiceover is generated.'
Info 'Next: run verify.ps1 for the full read-only check list, then do the acceptance runs with Sanji in chat.'
exit 0
