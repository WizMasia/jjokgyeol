param(
    [Parameter(Mandatory=$true)][string]$Package,
    [string]$PreviousPackage,
    [switch]$AllowInstalledWebView2
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Resolve-Package([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf) -or $Path -notlike '*.paf.exe') {
        throw "Expected an existing .paf.exe file: $Path"
    }
    $file = Get-Item -LiteralPath $Path
    $stream = $file.OpenRead()
    try {
        if ($stream.ReadByte() -ne 77 -or $stream.ReadByte() -ne 90) {
            throw "Expected a Windows executable: $($file.FullName)"
        }
    } finally { $stream.Dispose() }
    $file.FullName
}
$Package = Resolve-Package $Package
$firstPackage = if ($PreviousPackage) { Resolve-Package $PreviousPackage } else { $Package }
if (-not $IsWindows -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
    throw 'Run acceptance on Windows x64 with 64-bit PowerShell 7.'
}
. (Join-Path $PSScriptRoot 'webview2.ps1')
$installed = @(Get-InstalledWebView2Version)
if (-not $AllowInstalledWebView2) {
    $os = Get-CimInstance Win32_OperatingSystem
    $identity = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if ($os.ProductType -ne 1 -or $installed.Count -or
        $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Offline acceptance requires Windows 10/11 with no Evergreen WebView2 and an unelevated user.'
    }
}
Write-Host "Windows build $([Environment]::OSVersion.Version), Evergreen versions: $($installed -join ', ')"
Get-FileHash -LiteralPath $Package -Algorithm SHA256 | Format-List

$repo = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
$work = Join-Path ([IO.Path]::GetTempPath()) ('jjok-portable-' + [guid]::NewGuid().ToString('N'))
$parent = Join-Path $work '한글 원고'
$destination = Join-Path $parent 'JjokgyeolPortable'
$moved = Join-Path $work '이동한 쪽결'
$results = $work
. (Join-Path $PSScriptRoot 'run-check.ps1')

function Install-Package([string]$File, [int]$ExpectedExit = 0) {
    # PortableApps adds the AppID to the supplied parent directory.
    $installer = Start-Process -FilePath $File -ArgumentList ('/S /DESTINATION="{0}\"' -f $parent) -PassThru
    try {
        if (-not $installer.WaitForExit(300000)) { throw "PAF installation timed out: $File" }
        if ($installer.ExitCode -ne $ExpectedExit) {
            throw "PAF installer exited $($installer.ExitCode), expected ${ExpectedExit}: $File"
        }
    } finally {
        if (-not $installer.HasExited) { $installer.Kill($true); $installer.WaitForExit() }
        $installer.Dispose()
    }
}

function Test-PortableLaunch([string]$Folder, [string]$Name) {
    $status = Join-Path $work "$Name.txt"
    Run-Check $Name {
        pwsh -NoProfile -File (Join-Path $PSScriptRoot 'gui-smoke.ps1') -Executable (Join-Path $Folder 'App/Jjokgyeol/jjokgyeol-desktop.exe') -PortableRoot $Folder -StatusFile $status
    }
    if ((Get-Content -LiteralPath $status -Raw).Trim() -ne 'PASS') { throw "$Name GUI smoke did not pass" }
}

try {
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    Install-Package $firstPackage
    Test-PortableLaunch $destination 'first-launch'

    $data = Join-Path $destination 'Data'
    $sentinel = Join-Path $data 'acceptance-sentinel.txt'
    $token = [guid]::NewGuid().ToString()
    Set-Content -LiteralPath $sentinel -Value $token -NoNewline
    $profileMarker = Join-Path $data 'WebView2/acceptance-profile.txt'
    Set-Content -LiteralPath $profileMarker -Value $token -NoNewline
    Install-Package $Package

    $metadata = Get-Content -LiteralPath (Join-Path $destination 'App/AppInfo/appinfo.ini') -Raw
    $name = (Get-Item -LiteralPath $Package).Name
    if ($metadata -notmatch '(?m)^AppID=JjokgyeolPortable\r?$' -or
        $metadata -notmatch '(?m)^DisplayVersion=(?<version>[^\r\n]+)' -or
        $name -cne "JjokgyeolPortable_$($Matches.version).paf.exe") {
        throw "PAF metadata does not match its versioned filename: $name"
    }
    Move-Item -LiteralPath $destination -Destination $moved
    Test-PortableLaunch $moved 'relocated-launch'
    foreach ($marker in @('acceptance-sentinel.txt', 'WebView2/acceptance-profile.txt')) {
        if ((Get-Content -LiteralPath (Join-Path (Join-Path $moved 'Data') $marker) -Raw) -cne $token) {
            throw "Upgrade or relocation lost Data/$marker"
        }
    }
    $runtime = Join-Path $moved 'App/Jjokgyeol/runtime'
    $job = Join-Path $work 'worker-proof'
    Run-Check 'worker' { python (Join-Path $PSScriptRoot 'diagnostic.py') $runtime $job --packaged }
    Run-Check 'compare' {
        python (Join-Path $repo 'desktop/qa/compare_pdfs.py') (Join-Path $job 'response.pdf') (Join-Path $repo 'proofs/numbering/odd-even.pdf')
    }

    # An unsafe upgrade target must be rejected before touching its App tree.
    $outside = Join-Path $work 'untouched'
    [IO.Directory]::CreateDirectory($outside) | Out-Null
    $outsideFile = Join-Path $outside 'keep.txt'
    Set-Content -LiteralPath $outsideFile -Value $token -NoNewline
    [IO.Directory]::CreateDirectory($destination) | Out-Null
    $junction = Join-Path $destination 'App'
    New-Item -ItemType Junction -Path $junction -Target $outside | Out-Null
    try {
        Install-Package $Package 1
        if ((Get-Content -LiteralPath $outsideFile -Raw) -cne $token -or
            @(Get-ChildItem -LiteralPath $outside -Force).Count -ne 1) {
            throw 'Installer touched the target of a junction.'
        }
    } finally { [IO.Directory]::Delete($junction) }

    if ((@(Get-InstalledWebView2Version) -join ',') -ne ($installed -join ',')) {
        throw 'Evergreen WebView2 registration changed during portable acceptance.'
    }
    Write-Host 'PASS: portable install, fixed WebView2, Data retention, relocation and numbered PDF'
} finally {
    if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
}
