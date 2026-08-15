. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Command docker
Invoke-Compose up -d
Invoke-Compose ps
