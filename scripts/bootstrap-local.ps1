. (Join-Path $PSScriptRoot 'common.ps1')

Assert-Command docker
Assert-Command wsl
Assert-Command nvidia-smi
& docker compose version
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose is unavailable' }
& docker info *> $null
if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop is not running' }
& wsl --status
& nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader

if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Write-Host 'Created .env from the local-only example.'
}
if (-not (Test-Path -LiteralPath 'config.json')) {
    Copy-Item -LiteralPath 'config.example.json' -Destination 'config.json'
    Write-Host 'Created config.json from the local-only example.'
}
foreach ($directory in @('state', 'output', 'resume')) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

Invoke-Compose config --quiet
Invoke-Compose build
Invoke-Compose up -d ollama
& (Join-Path $PSScriptRoot 'pull-models.ps1')
$chatModel = if ($env:OLLAMA_CHAT_MODEL) { $env:OLLAMA_CHAT_MODEL } else { 'qwen3:8b' }
Invoke-Compose exec -T ollama ollama run $chatModel 'Reply only with LOCAL_OK'
Invoke-Compose up -d autopilot scheduler
& (Join-Path $PSScriptRoot 'test-gpu.ps1')
& (Join-Path $PSScriptRoot 'doctor.ps1')

Write-Host ''
Write-Host 'Local setup complete. Main commands:'
Write-Host '  docker compose run --rm autopilot autopilot scan'
Write-Host '  docker compose run --rm autopilot autopilot draft #1'
Write-Host '  docker compose run --rm autopilot autopilot export --min 60'
Write-Host '  docker compose run --rm -i autopilot autopilot mcp'
Write-Host '  docker compose run --rm autopilot autopilot doctor'
