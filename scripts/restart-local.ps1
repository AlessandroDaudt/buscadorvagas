. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Command docker
Invoke-Compose restart
Invoke-Compose ps
