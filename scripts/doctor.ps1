. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Command docker
Invoke-Compose run --rm autopilot autopilot doctor
