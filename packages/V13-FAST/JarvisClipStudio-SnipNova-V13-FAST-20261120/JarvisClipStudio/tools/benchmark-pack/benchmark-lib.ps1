<#
.SYNOPSIS
  SnipNova benchmark pack - pure logic library.

.DESCRIPTION
  Every function in this file is side-effect-light and testable off-machine:
  probe/loudnorm/sample/timing inputs come in as objects or file contents,
  results come out as objects or report text. The Windows- and Docker-facing
  work lives in run-benchmark-pack.ps1 and sampler.ps1.

  Honesty rules baked in (same as the Phase-1 benchmark):
  - A stage or metric that was not measured reports NOT OBSERVED / NOT
    SAMPLED. Nothing is ever estimated or interpolated.
  - The A/V figure is a container-level stream start offset from ffprobe.
    It is NOT a lip-sync measurement and is never labelled as one.
  - Loudness target is the app's own finishing target: -14 LUFS integrated
    (backend/src/auto_director/audio_studio.py LOUDNORM_AUTO_FILTER),
    PASS band +/-2 LU.

  Compatible with Windows PowerShell 5.1 and PowerShell 7+.
#>

function New-CheckResult {
    param([string]$Name, [string]$Status, [string]$Detail)
    return [pscustomobject]@{ Name = $Name; Status = $Status; Detail = $Detail }
}

function Get-ProbeChecks {
    <#
      Quality checks for one clip from its ffprobe JSON object.
      Expected export shape: 1080x1920 portrait, h264 video, AAC audio.
      $DbDuration (seconds, from generated_clips.duration) is optional;
      without it the duration check is NOT OBSERVED.
    #>
    param(
        [Parameter(Mandatory = $true)]$Probe,
        [double]$DbDuration = -1
    )
    $checks = @()
    $streams = @($Probe.streams)
    $video = $streams | Where-Object { $_.codec_type -eq 'video' } | Select-Object -First 1
    $audio = $streams | Where-Object { $_.codec_type -eq 'audio' } | Select-Object -First 1

    if (-not $video) {
        $checks += New-CheckResult 'Video stream' 'FAIL' 'no video stream found'
        return $checks
    }
    $checks += New-CheckResult 'Video stream' 'PASS' ("codec {0}" -f $video.codec_name)

    if ([int]$video.width -eq 1080 -and [int]$video.height -eq 1920) {
        $checks += New-CheckResult 'Resolution' 'PASS' ("{0}x{1}" -f $video.width, $video.height)
    } else {
        $checks += New-CheckResult 'Resolution' 'FAIL' ("{0}x{1} (expected 1080x1920)" -f $video.width, $video.height)
    }

    if ($video.codec_name -eq 'h264') {
        $checks += New-CheckResult 'Video codec' 'PASS' 'h264'
    } else {
        $checks += New-CheckResult 'Video codec' 'FAIL' ("{0} (expected h264)" -f $video.codec_name)
    }

    $encoder = $null
    if ($video.tags -and $video.tags.encoder) { $encoder = [string]$video.tags.encoder }
    if (-not $encoder) {
        $checks += New-CheckResult 'Encoder' 'NOT OBSERVED' 'no encoder tag in stream metadata'
    } elseif ($encoder -match 'nvenc') {
        $checks += New-CheckResult 'Encoder' 'PASS' ("{0} (NVENC hardware encode)" -f $encoder)
    } else {
        $checks += New-CheckResult 'Encoder' 'WARN' ("{0} (not NVENC - CPU encode or NVENC fallback)" -f $encoder)
    }

    if (-not $audio) {
        $checks += New-CheckResult 'Audio stream' 'FAIL' 'no audio stream found'
    } elseif ($audio.codec_name -eq 'aac') {
        $checks += New-CheckResult 'Audio stream' 'PASS' 'aac'
    } else {
        $checks += New-CheckResult 'Audio stream' 'FAIL' ("{0} (expected aac)" -f $audio.codec_name)
    }

    if ($Probe.format -and $Probe.format.duration) {
        $dur = [double]$Probe.format.duration
        if ($DbDuration -ge 0) {
            $delta = [math]::Abs($dur - $DbDuration)
            if ($delta -le 0.6) {
                $checks += New-CheckResult 'Duration vs plan' 'PASS' ("file {0:N2}s vs planned {1:N2}s" -f $dur, $DbDuration)
            } else {
                $checks += New-CheckResult 'Duration vs plan' 'FAIL' ("file {0:N2}s vs planned {1:N2}s (delta {2:N2}s)" -f $dur, $DbDuration, $delta)
            }
        } else {
            $checks += New-CheckResult 'Duration vs plan' 'NOT OBSERVED' ("file duration {0:N2}s; no planned duration supplied" -f $dur)
        }
        # Container-level A/V start offset. NOT a lip-sync measurement.
        if ($audio) {
            $vStart = 0.0; $aStart = 0.0
            if ($video.start_time) { $vStart = [double]$video.start_time }
            if ($audio.start_time) { $aStart = [double]$audio.start_time }
            $offsetMs = [math]::Abs($vStart - $aStart) * 1000.0
            if ($offsetMs -le 100.0) {
                $checks += New-CheckResult 'A/V container start offset' 'PASS' ("{0:N0} ms (stream start_time delta; not a lip-sync measure)" -f $offsetMs)
            } else {
                $checks += New-CheckResult 'A/V container start offset' 'FAIL' ("{0:N0} ms (stream start_time delta; not a lip-sync measure)" -f $offsetMs)
            }
        }
    } else {
        $checks += New-CheckResult 'Duration vs plan' 'NOT OBSERVED' 'no format duration in probe'
    }
    return $checks
}

function Get-LoudnormIntegrated {
    <#
      Extract integrated loudness (input_i, LUFS) from ffmpeg loudnorm
      print_format=json output text. Returns $null when absent.
    #>
    param([Parameter(Mandatory = $true)][string]$Text)
    $m = [regex]::Match($Text, '"input_i"\s*:\s*"(-?[0-9.]+)"')
    if ($m.Success) { return [double]$m.Groups[1].Value }
    return $null
}

function Get-LoudnessCheck {
    param([double]$Lufs)
    if ($Lufs -ge -16.0 -and $Lufs -le -12.0) {
        return New-CheckResult 'Loudness' 'PASS' ("{0:N2} LUFS integrated (target -14 +/-2)" -f $Lufs)
    }
    return New-CheckResult 'Loudness' 'FAIL' ("{0:N2} LUFS integrated (target -14 +/-2)" -f $Lufs)
}

function Read-SampleStats {
    <#
      Aggregate a sampler CSV. Columns:
      timestamp,gpu_util_pct,gpu_mem_used_mb,gpu_mem_total_mb,cpu_pct,ram_used_gb,ram_total_gb
      Blank cells are skipped per-metric. Missing/empty file = NOT SAMPLED.
    #>
    param([string]$CsvPath)
    $stats = [pscustomobject]@{
        Sampled       = $false
        Samples       = 0
        GpuUtilMean   = $null; GpuUtilPeak = $null
        VramPeakMb    = $null; VramTotalMb = $null
        CpuMean       = $null; CpuPeak     = $null
        RamUsedPeakGb = $null; RamTotalGb  = $null
    }
    if (-not $CsvPath -or -not (Test-Path $CsvPath)) { return $stats }
    $rows = @(Import-Csv $CsvPath)
    if ($rows.Count -eq 0) { return $stats }
    $stats.Sampled = $true
    $stats.Samples = $rows.Count

    function _Agg($values, [string]$kind) {
        $nums = @($values | Where-Object { $_ -ne $null -and "$_" -ne '' } | ForEach-Object { [double]$_ })
        if ($nums.Count -eq 0) { return $null }
        if ($kind -eq 'mean') { return [math]::Round(($nums | Measure-Object -Average).Average, 1) }
        return [math]::Round(($nums | Measure-Object -Maximum).Maximum, 1)
    }
    $stats.GpuUtilMean   = _Agg ($rows | ForEach-Object { $_.gpu_util_pct }) 'mean'
    $stats.GpuUtilPeak   = _Agg ($rows | ForEach-Object { $_.gpu_util_pct }) 'max'
    $stats.VramPeakMb    = _Agg ($rows | ForEach-Object { $_.gpu_mem_used_mb }) 'max'
    $stats.VramTotalMb   = _Agg ($rows | ForEach-Object { $_.gpu_mem_total_mb }) 'max'
    $stats.CpuMean       = _Agg ($rows | ForEach-Object { $_.cpu_pct }) 'mean'
    $stats.CpuPeak       = _Agg ($rows | ForEach-Object { $_.cpu_pct }) 'max'
    $stats.RamUsedPeakGb = _Agg ($rows | ForEach-Object { $_.ram_used_gb }) 'max'
    $stats.RamTotalGb    = _Agg ($rows | ForEach-Object { $_.ram_total_gb }) 'max'
    return $stats
}

# Stage keys the app itself records in tasks.stage_timings_json
# (backend video_service.py + task_service.py, verified on milestone-2).
$script:StageKeyOrder = @(
    @('download_seconds',     'Download'),
    @('transcription_seconds', 'Transcription (faster-whisper)'),
    @('analysis_seconds',      'AI analysis (LLM via LiteLLM)'),
    @('pipeline_seconds',      'Pipeline total (app-recorded)'),
    @('render_seconds',        'Render loop (app-recorded, incl. per-clip persistence + QC)')
)

function Get-StageRows {
    param($Timings, [double]$TotalWallSeconds = -1)
    $rows = @()
    foreach ($pair in $script:StageKeyOrder) {
        $key = $pair[0]; $label = $pair[1]
        $val = $null
        if ($Timings -and $Timings.PSObject.Properties.Name -contains $key) { $val = $Timings.$key }
        if ($null -ne $val) {
            $rows += [pscustomobject]@{ Stage = $label; Seconds = [double]$val; Display = ("{0:N1}s" -f [double]$val) }
        } else {
            $rows += [pscustomobject]@{ Stage = $label; Seconds = $null; Display = 'NOT OBSERVED' }
        }
    }
    if ($TotalWallSeconds -ge 0) {
        $rows += [pscustomobject]@{ Stage = 'Total wall time (DB started_at -> completed_at)'; Seconds = $TotalWallSeconds; Display = ("{0:N1}s" -f $TotalWallSeconds) }
    } else {
        $rows += [pscustomobject]@{ Stage = 'Total wall time (DB started_at -> completed_at)'; Seconds = $null; Display = 'NOT OBSERVED' }
    }
    return $rows
}

function Get-BaselineValues {
    <# Flatten one run into the comparable baseline numbers. #>
    param($StageRows, $SampleStats, [int]$ClipCount, $FirstClipLufs)
    $vals = [ordered]@{}
    foreach ($r in $StageRows) {
        switch -Wildcard ($r.Stage) {
            'Download*'        { $vals['download_s'] = $r.Seconds }
            'Transcription*'   { $vals['transcription_s'] = $r.Seconds }
            'AI analysis*'     { $vals['analysis_s'] = $r.Seconds }
            'Pipeline total*'  { $vals['pipeline_s'] = $r.Seconds }
            'Render loop*'     { $vals['render_s'] = $r.Seconds }
            'Total wall time*' { $vals['total_wall_s'] = $r.Seconds }
        }
    }
    $vals['clips_produced'] = $ClipCount
    if ($SampleStats -and $SampleStats.Sampled) {
        $vals['gpu_util_peak_pct'] = $SampleStats.GpuUtilPeak
        $vals['gpu_util_mean_pct'] = $SampleStats.GpuUtilMean
        $vals['vram_peak_mb']      = $SampleStats.VramPeakMb
        $vals['cpu_peak_pct']      = $SampleStats.CpuPeak
        $vals['cpu_mean_pct']      = $SampleStats.CpuMean
        $vals['ram_used_peak_gb']  = $SampleStats.RamUsedPeakGb
    }
    $vals['first_clip_lufs'] = $FirstClipLufs
    return $vals
}

function Format-Delta {
    param($Old, $New, [string]$Unit = 's')
    if ($null -eq $Old -or $null -eq $New) { return 'n/a (not measured in one of the runs)' }
    $d = [double]$New - [double]$Old
    $pct = ''
    if ([double]$Old -ne 0) { $pct = (" ({0:+0.0;-0.0}%)" -f (100.0 * $d / [double]$Old)) }
    return ("{0:+0.0;-0.0}{1}{2}" -f $d, $Unit, $pct)
}

function New-BenchmarkReport {
    <#
      Build report.md for one run and handle the stored baseline.
      $Data keys: Title, GeneratedAt, EvidenceNote, Specs (array of
      "label: value" strings), Workload (array of strings), CacheHit ($true/
      $false/$null), StageRows, SampleStats, ClipReports (array of
      @{Clip="clip 1 (file)"; Checks=@(...)}), FramesNote, DiskBeforeGb,
      DiskAfterGb, RunDir.
      Baseline behaviour: if baseline.json exists in $BaselineDir and
      -SetBaseline is not passed, a comparison section is written and the
      stored baseline is left untouched. If none exists, this run becomes
      the baseline and the report says so. With -SetBaseline the baseline
      is replaced and the report says so.
      Returns the report text.
    #>
    param(
        [Parameter(Mandatory = $true)]$Data,
        [Parameter(Mandatory = $true)][string]$ReportPath,
        [string]$BaselineDir = '',
        [switch]$SetBaseline
    )
    $lines = @()
    $lines += "# $($Data.Title)"
    $lines += ''
    $lines += "Generated: $($Data.GeneratedAt)"
    $lines += ''
    $lines += "> $($Data.EvidenceNote)"
    $lines += ''
    $lines += '## Machine'
    $lines += ''
    foreach ($s in $Data.Specs) { $lines += "- $s" }
    $lines += ''
    $lines += '## Workload'
    $lines += ''
    foreach ($w in $Data.Workload) { $lines += "- $w" }
    if ($null -ne $Data.CacheHit) {
        $lines += ''
        if ($Data.CacheHit) {
            $lines += 'WARNING: cache_hit = true for this task - transcript/analysis were reused, so download/transcription/analysis times are NOT representative. Re-run with a video the app has never processed.'
        } else {
            $lines += 'cache_hit = false - front stages really ran for this measurement.'
        }
    }
    $lines += ''
    $lines += '## Stage timings (app-recorded, tasks.stage_timings_json)'
    $lines += ''
    $lines += '| Stage | Time |'
    $lines += '|---|---|'
    foreach ($r in $Data.StageRows) { $lines += "| $($r.Stage) | $($r.Display) |" }
    $lines += ''
    $lines += '## Resource use during the run'
    $lines += ''
    $ss = $Data.SampleStats
    if ($ss -and $ss.Sampled) {
        $lines += "Samples: $($ss.Samples) (sampler CSV sits next to this report)"
        $lines += ''
        $lines += '| Metric | Mean | Peak |'
        $lines += '|---|---|---|'
        $lines += "| GPU utilisation | $($ss.GpuUtilMean)% | $($ss.GpuUtilPeak)% |"
        $lines += "| CPU utilisation | $($ss.CpuMean)% | $($ss.CpuPeak)% |"
        $lines += "| VRAM used | - | $($ss.VramPeakMb) MB of $($ss.VramTotalMb) MB |"
        $lines += "| System RAM used | - | $($ss.RamUsedPeakGb) GB of $($ss.RamTotalGb) GB |"
    } else {
        $lines += 'NOT SAMPLED - this run was attached after the task finished (or sampling failed). Stage timings above are still the app''s own records; utilisation numbers are never reconstructed after the fact.'
    }
    $lines += ''
    if ($null -ne $Data.DiskBeforeGb -and $null -ne $Data.DiskAfterGb) {
        $lines += "Disk free (app drive): $($Data.DiskBeforeGb) GB before -> $($Data.DiskAfterGb) GB after"
        $lines += ''
    }
    $lines += '## Output quality (ffprobe / ffmpeg loudnorm, per clip)'
    $lines += ''
    $firstLufs = $null
    foreach ($cr in $Data.ClipReports) {
        $lines += "### $($cr.Clip)"
        $lines += ''
        $lines += '| Check | Result | Detail |'
        $lines += '|---|---|---|'
        foreach ($c in $cr.Checks) {
            $lines += "| $($c.Name) | $($c.Status) | $($c.Detail) |"
            if ($c.Name -eq 'Loudness' -and $c.Detail -match '(-?[0-9.]+) LUFS') { $firstLufs = [double]$Matches[1] }
        }
        $lines += ''
    }
    if ($Data.FramesNote) { $lines += $Data.FramesNote; $lines += '' }

    # ---- baseline comparison
    $clipCount = @($Data.ClipReports).Count
    $current = Get-BaselineValues -StageRows $Data.StageRows -SampleStats $ss -ClipCount $clipCount -FirstClipLufs $firstLufs
    $baselinePath = $null
    if ($BaselineDir) { $baselinePath = Join-Path $BaselineDir 'baseline.json' }
    $lines += '## Comparison vs stored baseline'
    $lines += ''
    if ($baselinePath -and (Test-Path $baselinePath) -and -not $SetBaseline) {
        $base = Get-Content $baselinePath -Raw | ConvertFrom-Json
        $lines += "Baseline from: $($base.generated_at) ($($base.title))"
        $lines += ''
        $lines += '| Metric | Baseline | This run | Delta |'
        $lines += '|---|---|---|---|'
        $units = @{ download_s = 's'; transcription_s = 's'; analysis_s = 's'; pipeline_s = 's'; render_s = 's'; total_wall_s = 's'; clips_produced = ''; gpu_util_peak_pct = '%'; gpu_util_mean_pct = '%'; vram_peak_mb = ' MB'; cpu_peak_pct = '%'; cpu_mean_pct = '%'; ram_used_peak_gb = ' GB'; first_clip_lufs = ' LUFS' }
        foreach ($k in $current.Keys) {
            $old = $null
            if ($base.values.PSObject.Properties.Name -contains $k) { $old = $base.values.$k }
            $new = $current[$k]
            $oldD = if ($null -eq $old) { 'n/a' } else { "$old" }
            $newD = if ($null -eq $new) { 'n/a' } else { "$new" }
            $unit = $units[$k]; if ($null -eq $unit) { $unit = '' }
            $lines += "| $k | $oldD | $newD | $(Format-Delta $old $new $unit) |"
        }
        $lines += ''
        $lines += 'The stored baseline was NOT changed by this run. Re-run with -SetBaseline to replace it (do that only for a deliberate new reference, e.g. after a major version change).'
    } elseif ($baselinePath) {
        $payload = [ordered]@{ title = $Data.Title; generated_at = $Data.GeneratedAt; values = $current }
        ($payload | ConvertTo-Json -Depth 5) | Set-Content -Path $baselinePath -Encoding UTF8
        if ($SetBaseline) {
            $lines += 'baseline.json was REPLACED with this run (-SetBaseline).'
        } else {
            $lines += 'No baseline existed - this run was stored as baseline.json. Later runs will be compared against it.'
        }
    } else {
        $lines += 'No baseline directory configured for this run.'
    }
    $lines += ''
    $lines += '## Honesty notes'
    $lines += ''
    $lines += '- Download time is network + YouTube, not app speed. Analysis time includes LLM provider latency and any rate-limit backoff.'
    $lines += '- Render loop (app-recorded) includes per-clip persistence and QC, which run inside that loop in the code - it is not pure encode time.'
    $lines += '- The A/V figure is a container start offset only. Lip sync is judged by watching the clip (frame stills are saved next to this report).'
    $lines += '- First task after a worker restart pays the faster-whisper model load inside transcription.'

    $text = ($lines -join "`r`n") + "`r`n"
    $text | Set-Content -Path $ReportPath -Encoding UTF8
    return $text
}

function ConvertFrom-VramTelemetryLine {
    <#
      Parse one app log line in the Stage-0 VRAM telemetry format emitted
      by backend/src/media/vram.py:
        vram stage=<name> free_mb=<n> total_mb=<n>
      The line may carry any log prefix (timestamp, level, logger name).
      Returns an object, or $null for any other line — including the
      'free_mb=unavailable' DEBUG form, which carries no measurement and
      must never be aggregated as one.
    #>
    param([string]$Line)
    if (-not $Line) { return $null }
    $m = [regex]::Match($Line, 'vram stage=([A-Za-z0-9_]+) free_mb=(\d+) total_mb=(\d+)')
    if (-not $m.Success) { return $null }
    return [pscustomobject]@{
        Stage   = $m.Groups[1].Value
        FreeMb  = [int]$m.Groups[2].Value
        TotalMb = [int]$m.Groups[3].Value
    }
}

function Get-VramStageSummary {
    <#
      Aggregate VRAM telemetry lines (in log order) into one row per
      stage: first free observed, minimum free observed, sample count.
      Stages with no parseable line are simply absent from the result —
      the caller reports them NOT OBSERVED; nothing is estimated.
    #>
    param([string[]]$Lines)
    $order = New-Object System.Collections.ArrayList
    $byStage = @{}
    foreach ($line in $Lines) {
        $t = ConvertFrom-VramTelemetryLine $line
        if (-not $t) { continue }
        if (-not $byStage.ContainsKey($t.Stage)) {
            $byStage[$t.Stage] = [pscustomobject]@{
                Stage       = $t.Stage
                FirstFreeMb = $t.FreeMb
                MinFreeMb   = $t.FreeMb
                TotalMb     = $t.TotalMb
                Samples     = 0
            }
            [void]$order.Add($t.Stage)
        }
        $row = $byStage[$t.Stage]
        $row.Samples++
        if ($t.FreeMb -lt $row.MinFreeMb) { $row.MinFreeMb = $t.FreeMb }
    }
    return @($order | ForEach-Object { $byStage[$_] })
}

function ConvertFrom-MmSs {
    <#
      Parse a generated_clips timestamp ("MM:SS", tolerating "H:MM:SS")
      into seconds. Returns $null for anything unparseable — the caller
      reports NOT OBSERVED rather than guessing a range.
    #>
    param([string]$Text)
    if (-not $Text) { return $null }
    $parts = @($Text.Trim() -split ':')
    if ($parts.Count -lt 2 -or $parts.Count -gt 3) { return $null }
    $seconds = 0.0
    foreach ($part in $parts) {
        $value = 0.0
        if (-not [double]::TryParse($part, [ref]$value)) { return $null }
        $seconds = $seconds * 60.0 + $value
    }
    return $seconds
}

function Get-WindowOverlap {
    <#
      Overlap of a source-time window with one clip's source range,
      expressed in clip-local seconds (what ffmpeg -ss/-t need when
      measuring inside the exported clip file). $null when the window
      and the clip do not overlap at all.
    #>
    param(
        [double]$ClipStart, [double]$ClipEnd,
        [double]$WindowStart, [double]$WindowEnd
    )
    $start = [math]::Max($ClipStart, $WindowStart)
    $end = [math]::Min($ClipEnd, $WindowEnd)
    if ($end -le $start) { return $null }
    return [pscustomobject]@{
        LocalStart = $start - $ClipStart
        LocalEnd   = $end - $ClipStart
        Seconds    = $end - $start
    }
}

function Get-VolumeDetectMean {
    <#
      Parse ffmpeg volumedetect output ("mean_volume: -27.3 dB").
      Digital silence reports "-inf dB" and parses to negative
      infinity — a real measurement, not a parse failure. Returns
      $null when no mean_volume line is present.
    #>
    param([Parameter(Mandatory = $true)][string]$Text)
    $m = [regex]::Match($Text, 'mean_volume:\s*(-inf|-?[0-9.]+)\s*dB')
    if (-not $m.Success) { return $null }
    if ($m.Groups[1].Value -eq '-inf') { return [double]::NegativeInfinity }
    return [double]$m.Groups[1].Value
}

function Get-TranscriptWindowWords {
    <#
      Words from a transcript-cache object (the app's
      .transcript_cache.json shape: words carry text + start/end in
      MILLISECONDS) whose midpoint falls inside a source-time window
      given in seconds. Returns the word texts in order; an empty
      array is a real result (no words in the window), distinct from
      the caller never obtaining a cache at all (NOT OBSERVED there).
    #>
    param($Cache, [double]$WindowStart, [double]$WindowEnd)
    $hits = @()
    if (-not $Cache -or -not $Cache.words) { return $hits }
    foreach ($word in @($Cache.words)) {
        if ($null -eq $word.start -or $null -eq $word.end) { continue }
        $midSeconds = (([double]$word.start + [double]$word.end) / 2.0) / 1000.0
        if ($midSeconds -ge $WindowStart -and $midSeconds -lt $WindowEnd) {
            $hits += [string]$word.text
        }
    }
    return $hits
}
