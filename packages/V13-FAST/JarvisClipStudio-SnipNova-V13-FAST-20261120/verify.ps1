# SnipNova (JarvisClipStudio) - Consolidated v12.1 release post-install verification (2026-11-19)
#
# READ-ONLY. This script changes nothing: it only looks and reports.
# Every check prints one PASS or FAIL line, then a summary. Run it after
# install.ps1, and send Sanji the full output.
#
# Notes:
# - This package (Consolidated v12.1) carries branch next-milestone
#   @ 3900c3da: the v12 tree plus Claude's combined series, Sanji's
#   gate fixes and the UI overhaul - live clipping in the app
#   (sessions API, live-worker container, /live page, Groq
#   hosted-Whisper captions, fast VOD clipping of finished
#   streams), the licence-gated asset library + Openverse B-roll,
#   the quality steps (all switches OFF, dormant), the transition
#   library + burned-in caption detector, the OFL/Apache font pack
#   (THEBOLDFONT dropped), one Layout picker for every niche with
#   hook-title on/off, profanity masking, the 30 s minimum clip
#   rule, the plain-path music/SFX finish fix, the LiteLLM gateway
#   timeout, and the composer-hero UI overhaul. The check list
#   GREW for this package: three new migrations
#   (20261118_0001_live_clipping.sql,
#   20261118_0002_task_hook_title_enabled.sql,
#   20261119_0001_live_session_progress.sql - the schema rises to
#   55), 12 new feature-file checks (the live module, the live
#   API, the live repository, the live worker, profanity masking,
#   the audio finish, the asset library + licence manifest, the
#   transition library, the font-pack manifest, the Live page,
#   the Layout picker) and one new container check (live-worker).
#   No check was removed or weakened. The expected total is 218
#   (20 base + 55 + 55 + 88). Gates on this exact tree: Backend:
#   full gate on the final backend tip (5,634 passed / 0 failed /
#   262 skipped / 9 xfailed / 1 error - the error is the
#   no-database live-API setup, adjudicated by the
#   database-backed run on the same tip: 108 passed / 1 skipped;
#   Postgres tier 10 passed).;
#   Frontend: vitest 959 tests total, 0 assertion failures (every
#   red mark in full runs was a sandbox timeout that passes
#   solo), tsc 0 errors, next build 76/76 pages, changelog PASS.;
#   the licence gate ship-clean over all 29 shipped audio files;
#   and a one-combo smoke acceptance (Full AutoClip + Plain,
#   end-to-end) - reports/V121_INTEGRATION_REPORT.md and
#   reports/REAL_EXPORT_ACCEPTANCE_V121_SMOKE.md.
# - Your override file is compose.override.yml (the SHORT name). This
#   script checks for exactly that file.
# - The LiteLLM gateway port is NOT published to the host, so the gateway
#   is checked from INSIDE the backend container. (Checking
#   localhost:4000 from the host always fails and proves nothing.)
# - The frontend is a production build; the page check retries for up to
#   a minute because the first answer can be slow while it warms up.

[CmdletBinding()]
param(
    [string]$ProjectDir = 'D:\EditedTool\JarvisClipStudio'
)

$ErrorActionPreference = 'Continue'
$script:passCount = 0
$script:failCount = 0

function Report {
    param([bool]$Ok, [string]$Name, [string]$Detail)
    if ($Ok) {
        Write-Host "PASS: $Name$(if ($Detail) { " - $Detail" })" -ForegroundColor Green
        $script:passCount++
    } else {
        Write-Host "FAIL: $Name$(if ($Detail) { " - $Detail" })" -ForegroundColor Red
        $script:failCount++
    }
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
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 8 -ErrorAction Stop
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}
function Test-Http200Retried {
    param([string]$Url, [int]$Tries = 12)
    for ($i = 1; $i -le $Tries; $i++) {
        if (Test-Http200 -Url $Url) { return $true }
        Start-Sleep -Seconds 5
    }
    return $false
}

Write-Host 'SnipNova (JarvisClipStudio) - Consolidated v12.1 release post-install verification (read-only, changes nothing)'
Write-Host "Project folder: $ProjectDir"
Write-Host ''

if (-not (Test-Path $ProjectDir)) {
    Report $false 'Project folder exists' "not found: $ProjectDir"
    Write-Host ''
    Write-Host "Summary: $($script:passCount) passed, $($script:failCount) failed."
    exit 1
}
Report $true 'Project folder exists' $ProjectDir

# ---------------------------------------------------------- tree + migrations
$migrationsDir = Join-Path (Join-Path (Join-Path $ProjectDir 'backend') 'src') 'migrations'
$migrationsDir = Join-Path $migrationsDir 'sql'
$allMigrations = @(
    '20260302_0001_performance_schema.sql',
    '20260319_0002_generated_clips_task_order_unique.sql',
    '20260503_0001_app_settings.sql',
    '20260503_0002_app_settings_preference.sql',
    '20260507_0001_task_processing_columns.sql',
    '20260628_0001_api_keys.sql',
    '20260704_0001_clip_hook_title.sql',
    '20260722_0001_task_share_tokens.sql',
    '20260901_0001_social_publishing.sql',
    '20261011_0001_task_source_metadata.sql',
    '20261013_0001_media_deletions.sql',
    '20261013_0002_service_statuses.sql',
    '20261014_0001_media_storage_recovery.sql',
    '20261016_0001_email_deliveries.sql',
    '20261018_0001_pricing_minutes_legacy_hd.sql',
    '20261021_0001_stream_source_types.sql',
    '20261023_0001_rejected_source_attempts.sql',
    '20261025_0001_clip_exports.sql',
    '20261025_0002_minute_usage_ledger.sql',
    '20261025_0003_pending_plan_upgrade.sql',
    '20261025_0004_default_clip_length.sql',
    '20261025_0004_product_update_preferences.sql',
    '20261029_0001_upload_idempotency.sql',
    '20261029_0002_upload_idempotency_keys.sql',
    '20261103_0001_task_processing_checkpoints.sql',
    '20261105_0001_ai_edit_usage.sql',
    '20261105_0004_feature_flags.sql',
    '20261107_0001_niche_override.sql',
    '20261108_0001_music_override.sql',
    '20261108_0002_clip_qc.sql',
    '20261108_0003_learning_engine.sql',
    '20261108_0004_provider_manager.sql',
    '20261108_0005_clip_plan.sql',
    '20261108_0006_hook_previews.sql',
    '20261108_0007_target_clip_count.sql',
    '20261109_0001_publishing.sql',
    '20261109_0002_content_requirements.sql',
    '20261109_0002_task_music_mode.sql',
    '20261109_0003_watch_folder.sql',
    '20261109_0004_brand_kit.sql',
    '20261109_0005_creation_modes.sql',
    '20261109_0006_watch_folder_music_mode.sql',
    '20261110_0001_task_classification.sql',
    '20261110_0002_task_template_mode.sql',
    '20261110_0002_task_separation_opt_in.sql',
    '20261111_0001_channel_monitoring.sql',
    '20261111_0002_channel_monitoring_stage2.sql',
    '20261112_0001_task_broll_enabled.sql',
    '20261115_0001_recipes.sql',
    '20261115_0002_watch_folder_recipe_id.sql',
    '20261116_0001_task_framing_mode.sql',
    '20261117_0001_task_layout_template.sql',
    '20261118_0001_live_clipping.sql',
    '20261118_0002_task_hook_title_enabled.sql',
    '20261119_0001_live_session_progress.sql'
)
foreach ($m in $allMigrations) {
    Report (Test-Path (Join-Path $migrationsDir $m)) "Migration file in tree: $m" ''
}
$featureFiles = @(
    @('Viral Intelligence planner', 'backend\src\auto_director\clip_plan.py'),
    @('Beat-grid engine', 'backend\src\auto_director\beat_grid.py'),
    @('Visual moment selection', 'backend\src\auto_director\visual_moments.py'),
    @('Hook-variant previews', 'backend\src\auto_director\hook_variants.py'),
    @('Studio look overrides (grade/VFX)', 'backend\src\auto_director\look.py'),
    @('Reference Style user profiles', 'backend\src\auto_director\user_profiles.py'),
    @('Publishing workspace API', 'backend\src\api\routes\publishing.py'),
    @('Reference Styles API', 'backend\src\api\routes\reference_styles.py'),
    @('Local voiceover engine (Kokoro)', 'backend\src\media\voiceover.py'),
    @('Watch-folder autopilot API', 'backend\src\api\routes\watch_folder.py'),
    @('Brand Kit API', 'backend\src\api\routes\brand_kits.py'),
    @('Four creation modes engine', 'backend\src\auto_director\creation_modes.py'),
    @('Brand Kit engine', 'backend\src\auto_director\brand_kit.py'),
    @('Watch-folder repository', 'backend\src\repositories\watch_folder_repository.py'),
    @('Brand Kit repository', 'backend\src\repositories\brand_kit_repository.py'),
    @('Campaign content requirements', 'backend\src\auto_director\content_requirements.py'),
    @('RTX 4050 benchmark pack', 'tools\benchmark-pack\run-benchmark-pack.ps1'),
    @('Creation modes (frontend)', 'frontend\src\lib\creation-modes.ts'),
    @('Auto-niche classifier (fixed chain)', 'backend\src\auto_director\classify.py'),
    @('SFX upload - your own effects', 'backend\src\api\routes\sfx.py'),
    @('EDL renderer (info-card text fix)', 'backend\src\media\edl_render.py'),
    @('Reframe planner (face-in-blur guard)', 'backend\src\media\reframe_plan.py'),
    @('Reference Styles label confidence (frontend)', 'frontend\src\lib\reference-styles.ts'),
    @('Scored cover-frame selection', 'backend\src\media\cover_selection.py'),
    @('Task service (Find Moments auto-pick)', 'backend\src\services\task_service.py'),
    @('Studio editor document (per-lane hide/lock)', 'backend\src\editor_document.py'),
    @('Overlay lane compositing', 'backend\src\media\image_overlay.py'),
    @('Plain template mode (just cuts)', 'backend\src\auto_director\template_mode.py'),
    @('B-roll word-sense gating', 'backend\src\broll.py'),
    @('Voice/music separation engine', 'backend\src\media\separation.py'),
    @('Branded deliverable bundle (hook previews)', 'backend\src\deliverable_bundle.py'),
    @('VRAM stage telemetry + build-lite guards', 'backend\src\media\vram.py'),
    @('YouTube channel monitoring service', 'backend\src\services\channel_monitor_service.py'),
    @('Channel monitoring API', 'backend\src\api\routes\channel_monitors.py'),
    @('Channel monitoring settings UI', 'frontend\src\components\channel-monitors-section.tsx'),
    @('Reference caption-look measurement', 'backend\src\auto_director\reference_style.py'),
    @('Third-person hook-title guardrail', 'backend\src\auto_director\title_person.py'),
    @('Caption/title face guard', 'backend\src\media\face_text_guard.py'),
    @('B-roll vision veto (SigLIP, opt-in)', 'backend\src\media\broll_vision.py'),
    @('Split source-border trim', 'backend\src\media\border_trim.py'),
    @('Active-speaker targeting (opt-in)', 'backend\src\media\active_speaker.py'),
    @('Brief-from-screenshot OCR ingestion', 'backend\src\auto_director\brief_image.py'),
    @('Brief screenshot upload UI', 'frontend\src\components\content-brief-panel.tsx'),
    @('Multi-provider gateway ladder (LiteLLM config)', 'litellm_config.yaml'),
    @('B-roll per-task ON/OFF setting (Slice 1/toggle)', 'backend\src\auto_director\broll_setting.py'),
    @('Coverr stock source (B-roll source #2)', 'backend\src\media\coverr_broll.py'),
    @('Stock library with licence manifest', 'backend\src\media\stock_library.py'),
    @('Pixabay stock resolver v2 (concepts + photos)', 'backend\src\media\pixabay_broll.py'),
    @('B-roll review surface (editor panel)', 'frontend\src\components\editor\broll-panel.tsx'),
    @('Eco render mode (bounded ffmpeg threads)', 'backend\src\media\ffmpeg.py'),
    @('Task repository (settings read-back fix)', 'backend\src\repositories\task_repository.py'),
    @('Task-page degradation notice', 'frontend\src\components\app\degraded-analysis-notice.tsx'),
    @('Framing mode setting (auto/fit/crop)', 'backend\src\auto_director\framing_mode.py'),
    @('Person detection (YOLOX, face-evidence stack)', 'backend\src\media\person_detection.py'),
    @('Streamer Reaction journey (create preset)', 'frontend\src\lib\streamer-journey.ts'),
    @('Reaction moment picker (face/voice/gameplay fusion)', 'backend\src\auto_director\reaction_moments.py'),
    @('Gameplay moment detection', 'backend\src\auto_director\gameplay_moments.py'),
    @('Reaction hook titles', 'backend\src\auto_director\reaction_hooks.py'),
    @('Edit recommendations', 'backend\src\auto_director\edit_recommendations.py'),
    @('Facecam rectangle detection', 'backend\src\media\facecam_rect.py'),
    @('Gaming layout templates (face on top, game below)', 'backend\src\media\gaming_templates.py'),
    @('Gaming layout picker (create page)', 'frontend\src\components\home-app.tsx'),
    @('Galaxy starfield background', 'frontend\src\components\reactbits\AppBackground.tsx'),
    @('Bundled music bed - calm', 'assets\music\calm.mp3'),
    @('Bundled music bed - comedy', 'assets\music\comedy.mp3'),
    @('Bundled music bed - corporate', 'assets\music\corporate.mp3'),
    @('Bundled music bed - energetic', 'assets\music\energetic.mp3'),
    @('Bundled music bed - funk', 'assets\music\funk.mp3'),
    @('Bundled music bed - phonk', 'assets\music\phonk.mp3'),
    @('Bundled music bed - upbeat', 'assets\music\upbeat.mp3'),
    @('Bundled SFX - ding', 'assets\sfx\ding.wav'),
    @('Bundled SFX - impact', 'assets\sfx\impact.wav'),
    @('Bundled SFX - pop', 'assets\sfx\pop.wav'),
    @('Bundled SFX - riser', 'assets\sfx\riser.wav'),
    @('Bundled SFX - sting', 'assets\sfx\sting.wav'),
    @('Bundled SFX - whoosh', 'assets\sfx\whoosh.wav'),
    @('Live VOD fast clipping engine', 'backend\src\live\vod.py'),
    @('Live clipping API', 'backend\src\api\routes\live.py'),
    @('Live sessions repository', 'backend\src\repositories\live_repository.py'),
    @('Live worker', 'backend\src\workers\live_worker.py'),
    @('Profanity masking (captions + titles)', 'backend\src\media\profanity.py'),
    @('Plain-path music/SFX finish', 'backend\src\media\audio_finish.py'),
    @('Licence-gated asset library', 'backend\src\media\asset_library.py'),
    @('Asset licence manifest', 'assets\ASSET_MANIFEST.json'),
    @('Transition library (248 entries)', 'backend\src\media\transition_library.py'),
    @('Caption font pack manifest', 'backend\fonts\FONT_MANIFEST.json'),
    @('Live page (frontend)', 'frontend\src\app\live\page.tsx'),
    @('Layout picker (create page)', 'frontend\src\components\layout-picker.tsx')
)
foreach ($f in $featureFiles) {
    Report (Test-Path (Join-Path $ProjectDir $f[1])) "Feature present: $($f[0])" ''
}

$envFile = Join-Path $ProjectDir '.env'
Report (Test-Path $envFile) 'Your .env is in place' ''
Report (Test-Path (Join-Path $ProjectDir 'compose.override.yml')) 'Your compose.override.yml is in place (short name - correct)' ''

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Report $false 'docker on PATH' 'Docker Desktop does not appear to be running'
    Write-Host ''
    Write-Host "Summary: $($script:passCount) passed, $($script:failCount) failed."
    exit 1
}

# ------------------------------------------------------------- containers up
Push-Location $ProjectDir
try {
    Write-Host ''
    Write-Host '--- docker compose ps ---'
    & docker compose ps
    Write-Host '-------------------------'
    foreach ($svc in @('postgres', 'redis', 'backend', 'worker', 'ai-worker', 'live-worker', 'frontend', 'litellm')) {
        $cid = & docker compose ps -q $svc
        if ($LASTEXITCODE -ne 0 -or -not $cid) {
            Report $false "Container running: $svc" 'no container found (service may be optional or the stack is down)'
        } else {
            $state = & docker inspect --format '{{.State.Status}}' $cid
            Report ($state -eq 'running') "Container running: $svc" "state: $state"
        }
    }

    # ------------------------------------------------------------------ GPU
    $gpuOut = & docker compose exec -T ai-worker uv run python -c 'import torch; print(torch.cuda.is_available())' 2>&1
    $gpuText = ($gpuOut | Out-String)
    Report ($gpuText -match 'True') 'GPU visible to the AI worker (torch.cuda.is_available())' (($gpuText -split "`n")[0].Trim())

    # ---------------------------------------------------------------- NVENC
    & docker compose exec -T worker ffmpeg -hide_banner -loglevel error -f lavfi -i 'color=size=256x256:rate=1' -frames:v 1 -c:v h264_nvenc -f null -
    Report ($LASTEXITCODE -eq 0) 'NVENC hardware encoder works in the worker (256x256 test encode)' ''

    # ------------------------------------------- long-form TPM passthrough
    $tpm = (& docker compose exec -T worker printenv ANALYSIS_LONGFORM_TPM 2>$null | Out-String).Trim()
    Report ($tpm -eq '120000') 'Long-form analysis pacing reaches the worker (ANALYSIS_LONGFORM_TPM)' "live value: $(if ($tpm) { $tpm } else { '(not set)' })"

    # --------------------------------------------- voiceover toggle state
    $vo = (& docker compose exec -T worker printenv VOICEOVER_ENABLED 2>$null | Out-String).Trim()
    if ($vo -match '^(true|1)$') {
        Report $true 'Voiceover toggle state' 'VOICEOVER_ENABLED is ON in the worker (the ~315 MB Kokoro model downloads once on first use)'
    } else {
        Report $true 'Voiceover toggle state' 'voiceover is OFF (the default; VOICEOVER_ENABLED not set to true)'
    }

    # ------------------------------------------------- migrations in database
    $applied = & docker compose exec -T postgres psql -U supoclip -d supoclip -t -A -c "SELECT version FROM schema_migrations ORDER BY version;"
    if ($LASTEXITCODE -ne 0) {
        Report $false 'Migrations applied in the database' 'could not query schema_migrations (is the backend up? migrations are applied by the backend at start-up)'
    } else {
        $appliedText = ($applied | Out-String)
        foreach ($m in $allMigrations) {
            Report ($appliedText -match [regex]::Escape($m)) "Migration applied in the database: $m" ''
        }
    }

    # ------------------------------------- LiteLLM from inside the backend
    $liteOut = & docker compose exec -T backend python -c "import os,urllib.request; req=urllib.request.Request('http://litellm:4000/v1/models', headers={'Authorization':'Bearer ' + os.environ.get('OPENAI_API_KEY','')}); print(urllib.request.urlopen(req, timeout=15).status)" 2>$null
    Report ($LASTEXITCODE -eq 0 -and (($liteOut | Out-String) -match '200')) 'LiteLLM gateway model list (checked from inside the backend container)' ''
} finally { Pop-Location }

# -------------------------------------------------------------------- HTTP
$frontPort = Get-EnvValue -File $envFile -Name 'FRONTEND_PORT'
if (-not $frontPort) { $frontPort = '3107' }
Report (Test-Http200Retried -Url 'http://localhost:8000/health' -Tries 6) 'Backend health endpoint (http://localhost:8000/health)' ''
Report (Test-Http200Retried -Url 'http://localhost:8000/health/db' -Tries 6) 'Backend database health endpoint (http://localhost:8000/health/db)' ''
Report (Test-Http200Retried -Url "http://localhost:$frontPort" -Tries 12) "Frontend page (http://localhost:$frontPort, retried - production build warms up slowly)" ''

# ------------------------------------------------------------------ Ollama
try {
    $ollama = Invoke-WebRequest -Uri 'http://localhost:11434/api/tags' -UseBasicParsing -TimeoutSec 8 -ErrorAction Stop
    $hasQwen = ($ollama.Content -match 'qwen3:8b')
    Report $true 'Ollama reachable on the host (http://localhost:11434)' $(if ($hasQwen) { 'qwen3:8b model present' } else { 'qwen3:8b model NOT listed - check: ollama list' })
} catch {
    Report $false 'Ollama reachable on the host (http://localhost:11434)' 'no answer - is Ollama running? The local fallback needs it.'
}

Write-Host ''
Write-Host "Summary: $($script:passCount) passed, $($script:failCount) failed."
Write-Host 'Send Sanji this full output. A FAIL here does not mean the install is broken - several checks depend on the stack having finished starting.'
if ($script:failCount -gt 0) { exit 1 } else { exit 0 }
