param([Parameter(Mandatory = $true)][string]$Archive, [switch]$Force)
. (Join-Path $PSScriptRoot 'common.ps1')
if (-not $Force) { throw 'Restore overwrites known local files. Re-run with -Force after reviewing the archive.' }
$archivePath = [System.IO.Path]::GetFullPath($Archive)
if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) { throw "Archive not found: $archivePath" }
& (Join-Path $PSScriptRoot 'backup-local.ps1')
$temporary = Join-Path ([System.IO.Path]::GetTempPath()) ("autopilot-restore-{0}" -f [guid]::NewGuid())
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    Expand-Archive -LiteralPath $archivePath -DestinationPath $temporary
    foreach ($name in @('.env', 'config.json', 'companies.json', 'config', 'resume', 'state', 'output')) {
        $source = Join-Path $temporary $name
        if (Test-Path -LiteralPath $source) {
            $destination = Join-Path $script:ProjectRoot $name
            if (Test-Path -LiteralPath $source -PathType Container) {
                New-Item -ItemType Directory -Path $destination -Force | Out-Null
                Get-ChildItem -LiteralPath $source -Force | Copy-Item -Destination $destination -Recurse -Force
            } else {
                Copy-Item -LiteralPath $source -Destination $destination -Force
            }
        }
    }
    $volumeArchives = Get-ChildItem -LiteralPath $temporary -Filter 'autopilot_*.tar.gz' -File
    if ($volumeArchives) {
        Assert-Command docker
        & docker info *> $null
        if ($LASTEXITCODE -ne 0) { throw 'Docker is required to restore the archived named volumes' }
        Invoke-Compose stop autopilot scheduler
        foreach ($volumeArchive in $volumeArchives) {
            $logicalName = $volumeArchive.Name -replace '\.tar\.gz$', ''
            if ($logicalName -notin @('autopilot_state', 'autopilot_output')) { continue }
            $volumeName = "autopilot-jobhunt_$logicalName"
            & docker volume create $volumeName *> $null
            if ($LASTEXITCODE -ne 0) { throw "Could not create Docker volume: $volumeName" }
            $pythonCode = "import pathlib,shutil,tarfile; r=pathlib.Path('/target').resolve(); t=tarfile.open('/backup/$($volumeArchive.Name)','r:gz'); m=t.getmembers(); assert all((r/x.name).resolve()==r or r in (r/x.name).resolve().parents for x in m), 'unsafe archive path'; [(shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()) for p in r.iterdir()]; t.extractall(r,members=m,filter='data'); t.close()"
            & docker run --rm --entrypoint python `
                --mount "type=volume,source=$volumeName,target=/target" `
                --mount "type=bind,source=$temporary,target=/backup,readonly" `
                python:3.12-slim -c $pythonCode
            if ($LASTEXITCODE -ne 0) { throw "Could not restore Docker volume: $volumeName" }
        }
    }
} finally {
    $resolvedTemporary = [System.IO.Path]::GetFullPath($temporary)
    $systemTemporary = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    if ($resolvedTemporary.StartsWith($systemTemporary, [System.StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolvedTemporary -Recurse -Force
    }
}
Write-Host 'Restore complete. A pre-restore backup was created automatically.'
