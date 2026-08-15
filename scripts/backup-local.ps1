param([string]$Destination)
. (Join-Path $PSScriptRoot 'common.ps1')
$backupDirectory = Join-Path $script:ProjectRoot 'backups'
New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
if (-not $Destination) {
    $Destination = Join-Path $backupDirectory ("autopilot-local-{0}.zip" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
}
$destinationPath = [System.IO.Path]::GetFullPath($Destination)
$hostPaths = @('.env', 'config.json', 'companies.json', 'config', 'resume', 'state', 'output') |
    Where-Object { Test-Path -LiteralPath $_ }
$temporary = Join-Path ([System.IO.Path]::GetTempPath()) ("autopilot-volume-backup-{0}" -f [guid]::NewGuid())
New-Item -ItemType Directory -Path $temporary | Out-Null
$volumeArchives = @()
$dockerAvailable = $false
if (Get-Command docker -ErrorAction SilentlyContinue) {
    & docker info *> $null
    $dockerAvailable = $LASTEXITCODE -eq 0
}
try {
    if ($dockerAvailable) {
        foreach ($logicalName in @('autopilot_state', 'autopilot_output')) {
            $volumeName = "autopilot-jobhunt_$logicalName"
            & docker volume inspect $volumeName *> $null
            if ($LASTEXITCODE -ne 0) { continue }
            $archiveName = "$logicalName.tar.gz"
            $pythonCode = "import shutil; shutil.make_archive('/backup/$logicalName', 'gztar', '/source')"
            & docker run --rm --entrypoint python `
                --mount "type=volume,source=$volumeName,target=/source,readonly" `
                --mount "type=bind,source=$temporary,target=/backup" `
                python:3.12-slim -c $pythonCode
            if ($LASTEXITCODE -ne 0) { throw "Could not back up Docker volume: $volumeName" }
            $volumeArchives += Join-Path $temporary $archiveName
        }
    }
    $archiveInputs = @($hostPaths) + @($volumeArchives)
    if (-not $archiveInputs) { throw 'No local data was found to back up' }
    Compress-Archive -LiteralPath $archiveInputs -DestinationPath $destinationPath -CompressionLevel Optimal
} finally {
    $resolvedTemporary = [System.IO.Path]::GetFullPath($temporary)
    $systemTemporary = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    if ($resolvedTemporary.StartsWith($systemTemporary, [System.StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolvedTemporary -Recurse -Force
    }
}
Write-Host "Backup created: $destinationPath"
