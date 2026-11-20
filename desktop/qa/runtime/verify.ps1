param(
    [Parameter(Mandatory=$true)][string]$Resources,
    [Parameter(Mandatory=$true)][string]$Work
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
Set-Location $root
$Work = [IO.Path]::GetFullPath($Work)
$results = Join-Path $Work 'results'
New-Item -ItemType Directory -Force $results | Out-Null
$failed = $false
function Run-Check([string]$Name, [scriptblock]$Command) {
    Write-Host "=== $Name ==="
    & $Command 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath (Join-Path $results "$Name.log")
    if ($LASTEXITCODE -ne 0) { throw "$Name failed: $LASTEXITCODE" }
}
try {
    try { Run-Check 'rust-tests' { cargo test --locked --manifest-path desktop/Cargo.toml } } catch { $failed = $true; $_ | Tee-Object -FilePath (Join-Path $results 'rust-failure.txt') }
    Run-Check 'python-dependencies' { python -m pip install markdown-it-py==4.3.0 mdurl==0.1.2 pdfminer.six==20260107 pypdf==7.1.1 }
    Run-Check 'pyinstaller-dependencies' { python -m pip install --require-hashes --only-binary=:all: -r desktop/worker/build-requirements.lock }
    foreach ($name in @('TEXINPUTS', 'TEXMFCNF', 'TEXMFHOME', 'TEXMFLOCAL', 'TEXMFVAR', 'TEXMFCONFIG', 'LUAINPUTS')) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
    $extracted = Join-Path $Work 'resources'
    New-Item -ItemType Directory -Force $extracted | Out-Null
    Run-Check 'extract' { tar -xf $Resources -C $extracted }
    Run-Check 'setup-tests' { python -m unittest discover -s desktop/qa/runtime -p 'test_*.py' }
    Run-Check 'comparator-tests' { python -m unittest discover -s desktop/qa -p 'test_*.py' }
    Run-Check 'runtime' { python desktop/qa/runtime/setup.py $extracted (Join-Path $Work 'runtime') }
    Run-Check 'worker-build' { python -m PyInstaller --clean --noconfirm desktop/worker/worker.spec }
    Copy-Item -Recurse dist/jjokgyeol-worker (Join-Path $Work 'runtime/worker')
    # Ensure LuaTeX cannot read caches from a user's TeX installation.
    $env:TEXMFCACHE = Join-Path $Work 'runtime/texlive/texmf-var'
    Run-Check 'render' { cargo run --locked --manifest-path desktop/Cargo.toml -- --runtime (Join-Path $Work 'runtime') --source proofs/numbering/manuscript.md --style proofs/numbering/odd-even.json --output (Join-Path $results 'odd-even.pdf') }
    Run-Check 'compare' { python desktop/qa/compare_pdfs.py (Join-Path $results 'odd-even.pdf') proofs/numbering/odd-even.pdf }
} catch {
    $failed = $true
    "$_" | Tee-Object -FilePath (Join-Path $results 'failure.txt')
} finally {
    $texLog = Join-Path $Work 'runtime/texlive/texmf-var/web2c/luahbtex/lualatex.log'
    if (Test-Path $texLog) { Copy-Item $texLog (Join-Path $results 'format.log') }
    if ($failed) { 'FAIL' | Set-Content (Join-Path $results 'result.txt'); exit 1 }
    'PASS' | Set-Content (Join-Path $results 'result.txt')
}
