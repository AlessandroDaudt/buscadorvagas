. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Command docker
Write-Host 'Host GPU:'
& nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
if ($LASTEXITCODE -ne 0) { throw 'Host NVIDIA GPU is unavailable' }
Write-Host 'Ollama GPU devices:'
Invoke-Compose exec -T ollama sh -c 'ls -l /dev/nvidia* 2>/dev/null || exit 1'
Write-Host 'Loaded Ollama models (PROCESSOR should show GPU after inference):'
Invoke-Compose exec -T ollama ollama ps
