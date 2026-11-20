param(
    [Parameter(Mandatory=$true)][string]$Executable,
    [Parameter(Mandatory=$true)][string]$StatusFile
)
$ErrorActionPreference = 'Stop'
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class DesktopAccess {
    [DllImport("user32.dll", SetLastError=true)] public static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
    [DllImport("user32.dll")] public static extern bool CloseDesktop(IntPtr desktop);
}
'@
$desktop = [DesktopAccess]::OpenInputDesktop(0, $false, 1)
$interactive = [Environment]::UserInteractive -and $desktop -ne [IntPtr]::Zero
if ($desktop -ne [IntPtr]::Zero) { [void][DesktopAccess]::CloseDesktop($desktop) }
$process = $null
try {
    $process = Start-Process -FilePath $Executable -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds($(if ($interactive) { 30 } else { 5 }))
    while ([DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 500
        $process.Refresh()
        if ($process.HasExited) { throw "GUI exited before a window appeared (exit $($process.ExitCode))" }
        if ($process.MainWindowTitle -eq '쪽결') {
            'PASS' | Set-Content -Encoding utf8 $StatusFile
            Write-Host 'GUI window title observed'
            exit 0
        }
    }
    if (-not $interactive) {
        'INCONCLUSIVE: input desktop unavailable' | Set-Content -Encoding utf8 $StatusFile
        Write-Host '::warning::GUI smoke inconclusive: input desktop unavailable'
        exit 0
    }
    throw 'GUI window title not observed within 30 seconds'
} catch {
    "FAIL: $_" | Set-Content -Encoding utf8 $StatusFile
    throw
} finally {
    if ($process) {
        $process.Refresh()
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    }
}
