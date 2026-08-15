. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Command docker
Invoke-Compose down
Write-Host 'Containers stopped. Named volumes and local files were preserved.'
