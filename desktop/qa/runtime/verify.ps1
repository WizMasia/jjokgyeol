param(
    [Parameter(Mandatory=$true)][string]$Resources,
    [Parameter(Mandatory=$true)][string]$Work
)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
Set-Location $root
$Work = [IO.Path]::GetFullPath($Work)
$results = Join-Path $Work 'results'
New-Item -ItemType Directory -Force $results | Out-Null
'NOT RUN: proof comparison did not complete' | Set-Content (Join-Path $results 'gui-smoke-status.txt')
$failed = $false
. (Join-Path $PSScriptRoot 'run-check.ps1')

try {
    Run-Check 'check-exit-propagation' { pwsh -NoProfile -File (Join-Path $PSScriptRoot 'test-check.ps1') }
    try { Run-Check 'rust-tests' { cargo test --locked --manifest-path desktop/Cargo.toml } } catch { $failed = $true; $_ | Tee-Object -FilePath (Join-Path $results 'rust-failure.txt') }
    Run-Check 'python-dependencies' { python -m pip install --require-hashes --only-binary=:all: -r desktop/worker/requirements.lock -r desktop/qa/requirements-compare.lock }
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
    $renderCompleted = $false
    $renderError = $null
    $previousDiagnostics = $env:JJOKGYEOL_TEST_DIAGNOSTICS
    $env:JJOKGYEOL_TEST_DIAGNOSTICS = '1'
    try {
        Run-Check 'render' { cargo run --locked --manifest-path desktop/Cargo.toml -- --runtime (Join-Path $Work 'runtime') --source proofs/numbering/manuscript.md --style proofs/numbering/odd-even.json --output (Join-Path $results 'odd-even.pdf') }
        $renderCompleted = $true
    } catch {
        $renderError = $_
    } finally {
        if ($null -eq $previousDiagnostics) { Remove-Item Env:JJOKGYEOL_TEST_DIAGNOSTICS -ErrorAction SilentlyContinue }
        else { $env:JJOKGYEOL_TEST_DIAGNOSTICS = $previousDiagnostics }
    }
    if ($renderCompleted) {
        try { Run-Check 'compare' { python desktop/qa/compare_pdfs.py (Join-Path $results 'odd-even.pdf') proofs/numbering/odd-even.pdf } }
        catch {
            $failed = $true
            "$_" | Tee-Object -FilePath (Join-Path $results 'compare-failure.txt')
        }
    } else {
        $failed = $true
        $job = Join-Path $Work 'diagnostic-job'
        $packagedJob = Join-Path $Work 'packaged-diagnostic-job'
        try {
            try { Run-Check 'packaged-diagnostic' { python desktop/qa/runtime/diagnostic.py (Join-Path $Work 'runtime') $packagedJob --packaged } }
            catch { "Packaged diagnostic rerun failed: $_" | Tee-Object -FilePath (Join-Path $results 'packaged-diagnostic-failure.txt') }
            foreach ($name in @('diagnostic-book.log', 'diagnostic-exception.txt', 'response.pdf')) {
                $file = Join-Path $packagedJob $name
                if (Test-Path $file) { Copy-Item $file (Join-Path $results "packaged-$name") }
            }
            try { Run-Check 'render-diagnostic' { python desktop/qa/runtime/diagnostic.py (Join-Path $Work 'runtime') $job } }
            catch { "Diagnostic rerun failed: $_" | Tee-Object -FilePath (Join-Path $results 'diagnostic-failure.txt') }
            foreach ($name in @('input.json', 'layout-error.txt', 'book.log', 'book.pdf')) {
                $file = Join-Path $job $name
                if (Test-Path $file) { Copy-Item $file (Join-Path $results "diagnostic-$name") }
            }
            if (Test-Path (Join-Path $job 'book.pdf')) {
                try { Run-Check 'source-diagnostic-compare' { python desktop/qa/compare_pdfs.py (Join-Path $job 'book.pdf') proofs/numbering/odd-even.pdf } }
                catch { "Source diagnostic comparison failed: $_" | Tee-Object -FilePath (Join-Path $results 'source-diagnostic-comparison-failure.txt') }
            }
            $log = Join-Path $job 'book.log'
            if (Test-Path $log) { Get-Content -Encoding utf8 -Tail 120 $log | Set-Content -Encoding utf8 (Join-Path $results 'diagnostic-book-tail.txt') }
        } catch {
            "Could not collect diagnostics: $_" | Tee-Object -FilePath (Join-Path $results 'diagnostic-collection-failure.txt')
        }
        "$renderError" | Tee-Object -FilePath (Join-Path $results 'render-failure.txt')
    }
    'NOT RUN: GUI build did not complete' | Set-Content (Join-Path $results 'gui-smoke-status.txt')
    $env:JJOKGYEOL_NATIVE_RUNTIME = Join-Path $Work 'runtime'
    Push-Location desktop/gui/frontend
    try {
        Run-Check 'frontend-install' { npm ci }
        Run-Check 'frontend-build' { npm run build }
        Run-Check 'frontend-tests' { npm test }
    } finally { Pop-Location }
    Run-Check 'gui-rust-tests' { cargo test --locked --manifest-path desktop/gui/Cargo.toml }
    Run-Check 'gui-build' { cargo build --locked --manifest-path desktop/gui/Cargo.toml }
    $metadata = cargo metadata --locked --no-deps --format-version 1 --manifest-path desktop/gui/Cargo.toml | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw "cargo metadata failed: $LASTEXITCODE" }
    $exe = Join-Path $metadata.target_directory 'debug/jjokgyeol-desktop.exe'
    Run-Check 'gui-smoke' { pwsh -NoProfile -File (Join-Path $PSScriptRoot 'gui-smoke.ps1') -Executable $exe -StatusFile (Join-Path $results 'gui-smoke-status.txt') }
} catch {
    $failed = $true
    "$_" | Tee-Object -FilePath (Join-Path $results 'failure.txt')
} finally {
    $texLog = Join-Path $Work 'runtime/texlive/texmf-var/web2c/luahbtex/lualatex.log'
    if (Test-Path $texLog) { Copy-Item $texLog (Join-Path $results 'format.log') }
    if ($failed) { 'FAIL' | Set-Content (Join-Path $results 'result.txt'); exit 1 }
    $smoke = Get-Content (Join-Path $results 'gui-smoke-status.txt') -Raw
    if ($smoke.Trim() -eq 'PASS') { 'PASS' | Set-Content (Join-Path $results 'result.txt') }
    else { 'PASS: GUI smoke inconclusive' | Set-Content (Join-Path $results 'result.txt') }
}
