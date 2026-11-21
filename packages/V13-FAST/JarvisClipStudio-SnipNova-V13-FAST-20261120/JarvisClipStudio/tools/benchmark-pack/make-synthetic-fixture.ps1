<#
.SYNOPSIS
  Generate the benchmark pack's synthetic fallback fixture.

.DESCRIPTION
  Renders a 180s 1280x720 animated test clip (testsrc2 + tone bursts)
  with the WORKER container's ffmpeg - nothing to install on the host -
  and copies it out to benchmark-runs\synthetic-fixture.mp4 (or
  -OutFile). Upload it through the app UI with the fixed settings in
  FIXTURE.md. See FIXTURE.md for its honest limits: it is a pipeline /
  render-load fixture, not a speech fixture.
#>
[CmdletBinding()]
param(
    [string]$ProjectDir = "",
    [string]$OutFile = ""
)

$ErrorActionPreference = "Stop"
$packDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ProjectDir) { $ProjectDir = (Resolve-Path (Join-Path $packDir '..\..')).Path }
if (-not $OutFile) { $OutFile = Join-Path $ProjectDir 'benchmark-runs\synthetic-fixture.mp4' }
Set-Location $ProjectDir
if (-not (Test-Path (Join-Path $ProjectDir 'docker-compose.yml'))) {
    throw "No docker-compose.yml in $ProjectDir - point -ProjectDir at the app root."
}
$outDir = Split-Path -Parent $OutFile
if ($outDir -and -not (Test-Path $outDir)) { New-Item -ItemType Directory -Force -Path $outDir | Out-Null }

Write-Host 'Rendering synthetic fixture inside the worker container (180s testsrc2 + tone)...'
docker compose exec -T worker ffmpeg -y `
    -f lavfi -i "testsrc2=duration=180:size=1280x720:rate=30" `
    -f lavfi -i "sine=frequency=440:duration=180" `
    -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest /tmp/snipnova-bench-synthetic.mp4
if ($LASTEXITCODE -ne 0) { throw 'ffmpeg failed inside the worker container - is the stack up? (docker compose up -d)' }
docker compose cp "worker:/tmp/snipnova-bench-synthetic.mp4" "$OutFile"
if ($LASTEXITCODE -ne 0) { throw 'docker compose cp failed to copy the fixture out.' }
Write-Host "Synthetic fixture written: $OutFile" -ForegroundColor Green
Write-Host 'Upload it via the app UI with the fixed settings from FIXTURE.md.'
