function Run-Check([string]$Name, [scriptblock]$Command) {
    Write-Host "=== $Name ==="
    $global:LASTEXITCODE = 0
    & $Command 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath (Join-Path $results "$Name.log")
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "$Name failed: $code" }
}
