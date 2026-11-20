param(
    [Parameter(Mandatory=$true)][string]$Executable,
    [Parameter(Mandatory=$true)][string]$StatusFile
)
$ErrorActionPreference = 'Stop'
Add-Type @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class DesktopAccess {
    [DllImport("user32.dll", SetLastError=true)] public static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
    [DllImport("user32.dll")] public static extern bool CloseDesktop(IntPtr desktop);
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr window);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll", SetLastError=true)] public static extern bool PostMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetWindowText(IntPtr window, StringBuilder text, int length);
    private delegate bool EnumWindowProc(IntPtr window, IntPtr parameter);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowProc callback, IntPtr parameter);
    public static IntPtr FindWindow(uint processId, string title, IntPtr exclude) {
        IntPtr found = IntPtr.Zero;
        EnumWindows((window, parameter) => {
            uint owner;
            GetWindowThreadProcessId(window, out owner);
            var text = new StringBuilder(512);
            GetWindowText(window, text, text.Capacity);
            if (owner == processId && window != exclude && IsWindowVisible(window) && text.ToString() == title) {
                found = window;
                return false;
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }
}
'@
$desktop = [DesktopAccess]::OpenInputDesktop(0, $false, 1)
$interactive = [Environment]::UserInteractive -and $desktop -ne [IntPtr]::Zero
if ($desktop -ne [IntPtr]::Zero) { [void][DesktopAccess]::CloseDesktop($desktop) }
$process = $null
$StatusFile = [IO.Path]::GetFullPath($StatusFile)
$observations = Join-Path (Split-Path -Parent $StatusFile) 'gui-smoke-observations.txt'
'Starting GUI smoke' | Set-Content -Encoding utf8 $observations
function Observe([string]$message) {
    $message | Add-Content -Encoding utf8 $observations
    Write-Host $message
}
function Check-Editor {
    $process.Refresh()
    if ($process.HasExited) { throw "GUI exited during smoke (exit $($process.ExitCode))" }
    if (-not [DesktopAccess]::IsWindow($editor)) { throw 'Editor window closed during smoke' }
}
try {
    $process = Start-Process -FilePath $Executable -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds($(if ($interactive) { 30 } else { 5 }))
    $editor = [IntPtr]::Zero
    while ([DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 500
        $process.Refresh()
        if ($process.HasExited) { throw "GUI exited before a window appeared (exit $($process.ExitCode))" }
        if ($process.MainWindowTitle -eq '쪽결') {
            $editor = $process.MainWindowHandle
            break
        }
    }
    if (-not $interactive) {
        'INCONCLUSIVE: input desktop unavailable' | Set-Content -Encoding utf8 $StatusFile
        Observe '::warning::GUI smoke inconclusive: input desktop unavailable'
        exit 0
    }
    if ($editor -eq [IntPtr]::Zero) { throw 'GUI window title not observed within 30 seconds' }
    Observe "Editor window observed: process $($process.Id), handle $editor, title 쪽결"

    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $root = [System.Windows.Automation.AutomationElement]::FromHandle($editor)
    if ($null -eq $root) { throw 'Editor window is unavailable to UI Automation' }
    $condition = [System.Windows.Automation.AndCondition]::new(
        [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty, '설정'),
        [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)
    )
    $button = $null
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ([DateTime]::UtcNow -lt $deadline -and $null -eq $button) {
        Check-Editor
        try { $button = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condition) }
        catch [System.Windows.Automation.ElementNotAvailableException] { }
        if ($null -eq $button) { Start-Sleep -Milliseconds 500 }
    }
    if ($null -eq $button) { throw 'Settings button not found in the editor accessibility tree within 30 seconds' }
    $pattern = $null
    if (-not $button.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) {
        throw 'Settings button does not support UI Automation InvokePattern'
    }
    $pattern.Invoke()
    Observe 'Invoked editor button: 설정 (UI Automation InvokePattern)'

    $settings = [IntPtr]::Zero
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ([DateTime]::UtcNow -lt $deadline) {
        Check-Editor
        $settings = [DesktopAccess]::FindWindow([uint32]$process.Id, '출판 설정', $editor)
        if ($settings -ne [IntPtr]::Zero) { break }
        Start-Sleep -Milliseconds 500
    }
    if ($settings -eq [IntPtr]::Zero) { throw 'Distinct publication settings window not observed within 30 seconds' }
    Observe "Settings window observed: process $($process.Id), handle $settings, title 출판 설정"
    if (-not [DesktopAccess]::PostMessage($settings, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)) {
        throw 'Could not send the settings window a system close request'
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ([DateTime]::UtcNow -lt $deadline -and [DesktopAccess]::IsWindow($settings)) {
        Check-Editor
        Start-Sleep -Milliseconds 500
    }
    if ([DesktopAccess]::IsWindow($settings)) { throw 'Settings window did not close within 30 seconds' }
    Check-Editor
    Observe "Settings window closed; editor window remains open: handle $editor"
    'PASS' | Set-Content -Encoding utf8 $StatusFile
    exit 0
} catch {
    Observe "FAIL: $_"
    "FAIL: $_" | Set-Content -Encoding utf8 $StatusFile
    throw
} finally {
    if ($process) {
        $process.Refresh()
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    }
}
