$ErrorActionPreference = 'Stop'
$results = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
New-Item -ItemType Directory $results | Out-Null
try {
    . (Join-Path $PSScriptRoot 'run-check.ps1')
    $caught = $false
    try { Run-Check 'failure' { & (Get-Process -Id $PID).Path -NoProfile -Command '[Console]::Error.WriteLine("diagnostic stderr"); exit 17' } } catch { $caught = $_.ToString() -match '17' }
    if (-not $caught) { throw 'nonzero native exit did not fail the check' }
    if (-not ((Get-Content (Join-Path $results 'failure.log') -Raw) -match 'diagnostic stderr')) { throw 'native stderr was not captured' }
    Run-Check 'success' { & (Get-Process -Id $PID).Path -NoProfile -Command 'exit 0' }
} finally {
    Remove-Item -Recurse -Force $results
}
