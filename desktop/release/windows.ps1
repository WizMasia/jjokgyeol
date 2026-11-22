param(
    [Parameter(Mandatory=$true)][string]$Resources,
    [Parameter(Mandatory=$true)][string]$OutputDir
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $IsWindows -or -not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
    throw 'This release must be built on Windows x64 with 64-bit PowerShell.'
}
if (-not [IO.Path]::IsPathFullyQualified($OutputDir)) { throw 'OutputDir must be an absolute path.' }
$Resources = (Resolve-Path -LiteralPath $Resources).Path
if (-not (Test-Path -LiteralPath $Resources -PathType Container)) { throw 'Resources must be a directory.' }
foreach ($name in @('texmf-dist', 'nanum', 'licenses')) {
    $path = Join-Path $Resources $name
    if (-not (Test-Path -LiteralPath $path -PathType Container) -or
        -not (Get-ChildItem -LiteralPath $path -File -Recurse | Select-Object -First 1)) {
        throw "Resources directory is missing or empty: $name"
    }
}
$OutputDir = [IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$output = Join-Path $OutputDir 'jjokgyeol-windows-x64-setup.exe'
if (Test-Path -LiteralPath $output) { throw "Output already exists; move it aside first: $output" }

# NSIS may otherwise upgrade an existing installation, even with a different /D path.
foreach ($key in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
                   'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
                   'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')) {
    $existing = @(Get-ItemProperty $key -ErrorAction SilentlyContinue | Where-Object {
        $_.PSObject.Properties['DisplayName'] -and $_.DisplayName -eq '쪽결'
    })
    if ($existing.Count) { throw 'An existing 쪽결 installation was found; use a clean Windows account or machine.' }
}

$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$work = Join-Path ([IO.Path]::GetTempPath()) ("jjokgyeol-release-" + [guid]::NewGuid().ToString('N'))
$runtime = Join-Path $work 'runtime'
$install = Join-Path $work 'installed app'
$target = Join-Path $work 'cargo-target'
$uninstaller = Join-Path $install 'uninstall.exe'
$installed = $false
$pushed = $false
$envNames = @('PYTHONUTF8', 'CARGO_TARGET_DIR', 'JJOKGYEOL_NATIVE_RUNTIME', 'JJOKGYEOL_TEST_DIAGNOSTICS',
    'TEXINPUTS', 'TEXMFCNF', 'TEXMFHOME', 'TEXMFLOCAL', 'TEXMFVAR', 'TEXMFCONFIG', 'TEXMFCACHE', 'LUAINPUTS')
$oldEnv = @{}
foreach ($name in $envNames) { $oldEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
function Invoke-Checked([string]$Command, [string[]]$Arguments) {
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Command failed with exit code $LASTEXITCODE" }
}
function Invoke-Installer([string]$Executable, [string[]]$Arguments) {
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "$Executable failed with exit code $($process.ExitCode)" }
}
try {
    New-Item -ItemType Directory -Path $work | Out-Null
    Push-Location $root
    $pushed = $true
    foreach ($name in $envNames) { [Environment]::SetEnvironmentVariable($name, $null, 'Process') }
    $env:PYTHONUTF8 = '1'
    $env:CARGO_TARGET_DIR = $target
    Invoke-Checked 'python' @('desktop/qa/runtime/setup.py', $Resources, $runtime)
    Copy-Item -LiteralPath (Join-Path $Resources 'licenses') -Destination (Join-Path $runtime 'licenses') -Recurse
    Invoke-Checked 'python' @('-m', 'PyInstaller', '--clean', '--noconfirm', '--distpath', (Join-Path $work 'dist'), '--workpath', (Join-Path $work 'pyinstaller'), 'desktop/worker/worker.spec')
    Copy-Item -LiteralPath (Join-Path $work 'dist/jjokgyeol-worker') -Destination (Join-Path $runtime 'worker') -Recurse
    Invoke-Checked 'python' @('desktop/gui/bundle.py', $runtime)
    $bundles = @(Get-ChildItem -LiteralPath (Join-Path $target 'release/bundle/nsis') -Filter '*.exe' -File)
    if ($bundles.Count -ne 1) { throw "Expected exactly one fresh NSIS installer, found $($bundles.Count)." }
    $installer = $bundles[0].FullName
    Invoke-Checked 'cargo' @('build', '--locked', '--release', '--manifest-path', 'desktop/Cargo.toml')
    $cli = Join-Path $target 'release/jjokgyeol-proof.exe'
    # NSIS requires /D to be the final argument, without quotes, even when it contains spaces.
    Invoke-Installer $installer @('/S', "/D=$install")
    $installed = $true
    $installedRuntime = Join-Path $install 'runtime'
    $gui = Join-Path $install 'jjokgyeol-desktop.exe'
    if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf) -or
        -not (Test-Path -LiteralPath $gui -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $installedRuntime 'runtime.json') -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $installedRuntime 'licenses') -PathType Container)) {
        throw "Installed files are missing from $install"
    }
    $inventory = Join-Path $work 'installed-files.json'
    Invoke-Checked 'python' @('desktop/release/verify_uninstall.py', 'snapshot', $install, $inventory)
    $pdf = Join-Path $work 'proof.pdf'
    Invoke-Checked $cli @('--runtime', $installedRuntime, '--source', 'proofs/numbering/manuscript.md', '--style', 'proofs/numbering/odd-even.json', '--output', $pdf)
    Invoke-Checked 'python' @('desktop/qa/compare_pdfs.py', $pdf, 'proofs/numbering/odd-even.pdf')
    $status = Join-Path $work 'gui-smoke-status.txt'
    Invoke-Checked 'pwsh' @('-NoProfile', '-File', (Join-Path $root 'desktop/qa/runtime/gui-smoke.ps1'), '-Executable', $gui, '-StatusFile', $status)
    $smoke = (Get-Content -LiteralPath $status -Raw).Trim()
    if ($smoke -ne 'PASS') { throw "Installed GUI smoke did not pass: $smoke" }
    Invoke-Installer $uninstaller @('/S', "_?=$install")
    $installed = $false
    # _?= keeps the uninstaller in place so the waited process is the real one.
    if (Test-Path -LiteralPath $uninstaller) { Remove-Item -LiteralPath $uninstaller -Force }
    Invoke-Checked 'python' @('desktop/release/verify_uninstall.py', 'verify', $install, $inventory)
    Copy-Item -LiteralPath $installer -Destination $output
    Write-Host "Release verified: $output"
} finally {
    $cleanupFailed = $false
    try {
        if (Test-Path -LiteralPath $uninstaller -PathType Leaf) {
            Invoke-Installer $uninstaller @('/S', "_?=$install")
        } elseif ($installed) {
            throw "Cannot uninstall test installation: $uninstaller is missing"
        }
    } catch {
        $cleanupFailed = $true
        Write-Warning "Test installation cleanup failed; retained $work for recovery."
        throw
    } finally {
        if ($pushed) { Pop-Location }
        foreach ($name in $envNames) { [Environment]::SetEnvironmentVariable($name, $oldEnv[$name], 'Process') }
        if (-not $cleanupFailed -and (Test-Path -LiteralPath $work)) { Remove-Item -LiteralPath $work -Recurse -Force }
    }
}
