$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$helper = Join-Path $PSScriptRoot '../../release/portable/Other/Source/prepare-webview.ps1'
$powershell = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
$work = Join-Path ([IO.Path]::GetTempPath()) ('jjok-paths-' + [guid]::NewGuid().ToString('N'))
$outside = Join-Path $work 'outside'
$sentinel = Join-Path $outside 'keep.ini'

function Test-Guard([string]$Root, [string[]]$Phase, [string]$Expected = '') {
    $log = & $powershell -NoProfile -NonInteractive -File $helper -Root $Root @Phase 2>&1
    $code = $LASTEXITCODE
    if ($Expected) {
        if ($code -ne 1 -or ($log | Out-String) -notlike "*$Expected*") {
            throw "Expected the guard to reject $Expected. Exit code $code. $log"
        }
    } elseif ($code -ne 0) {
        throw "Expected the guard to accept $Root. Exit code $code. $log"
    }
    if ((Get-Content -LiteralPath $sentinel -Raw) -cne 'keep') {
        throw 'Portable preflight changed the link target.'
    }
}

try {
    [void][IO.Directory]::CreateDirectory($outside)
    Set-Content -LiteralPath $sentinel -Value 'keep' -NoNewline
    $clean = Join-Path $work 'no platform/JjokgyeolPortable'
    [void][IO.Directory]::CreateDirectory($clean)
    Test-Guard $clean @('-ValidateInstall')
    if (@(Get-ChildItem -LiteralPath $clean -Force).Count) {
        throw 'Fresh installation preflight left files or directories behind.'
    }

    $cases = @(
        @{ Link = 'JjokgyeolPortable/Data/settings'; Type = 'Junction'; Phase = @() }
        @{ Link = 'JjokgyeolPortable/Data/settings/JjokgyeolPortableSettings.ini'; Type = 'HardLink'; Phase = @() }
        @{ Link = 'JjokgyeolPortable/Data/PortableApps.comLauncherRuntimeData-JjokgyeolPortable.ini'; Type = 'HardLink'; Phase = @() }
        @{ Link = 'PortableApps.com'; Type = 'Junction'; Phase = @('-ValidateInstall') }
        @{ Link = 'PortableApps.com/Data'; Type = 'Junction'; Phase = @('-FinishInstall') }
        @{ Link = 'PortableApps.com/Data/NewApp.ini'; Type = 'HardLink'; Phase = @('-ValidateInstall') }
    )
    foreach ($case in $cases) {
        $parent = Join-Path $work ([guid]::NewGuid().ToString('N'))
        $root = Join-Path $parent 'JjokgyeolPortable'
        $link = [IO.Path]::GetFullPath((Join-Path $parent $case.Link))
        [void][IO.Directory]::CreateDirectory($root)
        [void][IO.Directory]::CreateDirectory((Split-Path -Parent $link))
        $target = if ($case.Type -eq 'Junction') { $outside } else { $sentinel }
        New-Item -ItemType $case.Type -Path $link -Target $target | Out-Null
        try {
            Test-Guard $root $case.Phase "Linked item in portable path: $link"
        } finally {
            if ($case.Type -eq 'Junction') { [IO.Directory]::Delete($link) }
        }
    }

    $settings = Join-Path $clean 'Data/settings'
    [void][IO.Directory]::CreateDirectory($settings)
    $ini = Join-Path $settings 'JjokgyeolPortableSettings.ini'
    Set-Content -LiteralPath $ini -Value 'keep' -NoNewline
    [IO.File]::SetAttributes($ini, [IO.FileAttributes]::ReadOnly)
    try { Test-Guard $clean @('-ValidateInstall') "Cannot write $ini" }
    finally { [IO.File]::SetAttributes($ini, [IO.FileAttributes]::Normal) }
    if ((Get-Content -LiteralPath $ini -Raw) -cne 'keep') { throw 'Preflight changed the settings file.' }
    Test-Guard $clean @('-ValidateInstall')
    Write-Host 'PASS: launcher and installer write-path guards'
} finally {
    if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
}
