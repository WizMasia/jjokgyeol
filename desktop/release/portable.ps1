param(
    [Parameter(Mandatory=$true)][string]$Executable,
    [Parameter(Mandatory=$true)][string]$Runtime,
    [Parameter(Mandatory=$true)][string]$OutputDir
)
$ErrorActionPreference = 'Stop'
& python (Join-Path $PSScriptRoot 'portable.py') $Executable $Runtime $OutputDir
if ($LASTEXITCODE -ne 0) { throw "Portable packaging failed with exit code $LASTEXITCODE" }
