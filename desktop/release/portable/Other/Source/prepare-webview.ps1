param(
    [Parameter(Mandatory=$true)][string]$Root,
    [switch]$ValidateInstall,
    [switch]$FinishInstall
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

try {
    $architecture = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    if ($architecture -ne 'AMD64' -or [Environment]::OSVersion.Version.Build -lt 19045) {
        throw 'Requires Windows 10 22H2 or later on x64.'
    }
    if ($Root -notmatch '^[A-Z]:\\[^\\]' -or $Root.Substring(2).Contains(':') -or
        [IO.Path]::GetFullPath($Root).TrimEnd('\') -ne $Root -or
        @($Root.Split('\') | Where-Object { $_ -match '[ .]$' }).Count) {
        throw "Expected a canonical local folder: $Root"
    }
    $directory = [IO.DirectoryInfo]::new($Root)
    $drive = [IO.DriveInfo]::new($directory.Root.FullName)
    if ($drive.DriveType -ne 'Fixed' -or $drive.DriveFormat -ne 'NTFS') {
        throw 'Expected a local fixed NTFS drive.'
    }
    function Confirm-Item($item) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            ($item -isnot [IO.DirectoryInfo] -and $item.LinkType -eq 'HardLink')) {
            throw "Linked item in portable path: $($item.FullName)"
        }
    }
    function Get-ExistingItem($path) {
        try { Get-Item -LiteralPath $path -Force }
        catch [System.Management.Automation.ItemNotFoundException] { }
    }
    function Confirm-Path([string]$Path, [switch]$Directory) {
        $item = Get-ExistingItem $Path
        if ($null -ne $item) {
            Confirm-Item $item
            if ($Directory -ne ($item -is [IO.DirectoryInfo])) { throw "Unexpected item type: $Path" }
            $item
        }
    }
    function Test-DirectoryWrite([string]$Path) {
        $probe = Join-Path $Path ([IO.Path]::GetRandomFileName())
        try {
            $stream = [IO.File]::Open($probe, 'CreateNew', 'Write', 'None')
            try { $stream.Dispose() }
            finally { [IO.File]::Delete($probe) }
        }
        catch { throw "Cannot write in $Path. $($_.Exception.Message)" }
    }
    function Test-IniWrite([string]$Path) {
        if ($null -ne (Confirm-Path $Path)) {
            try { [IO.File]::Open($Path, 'Open', 'Write', 'ReadWrite').Dispose() }
            catch { throw "Cannot write $Path. $($_.Exception.Message)" }
        }
    }
    function Confirm-Tree($directory) {
        foreach ($item in Get-ChildItem -LiteralPath $directory -Force) {
            Confirm-Item $item
            if ($item -is [IO.DirectoryInfo]) { Confirm-Tree $item.FullName }
        }
    }
    $ancestors = [Collections.Generic.Stack[string]]::new()
    for ($parent = $directory; $null -ne $parent; $parent = $parent.Parent) {
        $ancestors.Push($parent.FullName)
    }
    foreach ($path in $ancestors) {
        $item = Get-ExistingItem $path
        if ($null -eq $item) { break }
        Confirm-Item $item
        if ($item -isnot [IO.DirectoryInfo]) { throw "Expected a directory: $path" }
    }
    $data = Join-Path $Root 'Data'
    $profile = Join-Path $data 'WebView2'
    if ($null -ne (Confirm-Path $data -Directory)) {
        $settings = Join-Path $data 'settings'
        if ($null -ne (Confirm-Path $settings -Directory)) {
            Test-IniWrite (Join-Path $settings 'JjokgyeolPortableSettings.ini')
            Test-DirectoryWrite $settings
        }
        Test-IniWrite (Join-Path $data 'PortableApps.comLauncherRuntimeData-JjokgyeolPortable.ini')
        if ($null -ne (Confirm-Path $profile -Directory)) {
            Confirm-Tree $profile
            Test-DirectoryWrite $profile
        }
        Test-DirectoryWrite $data
    } elseif ($ValidateInstall) {
        # The official installer has already selected the destination and may have created it.
        Test-DirectoryWrite $Root
    }
    if ($ValidateInstall -or $FinishInstall) {
        $eula = Join-Path $data 'PortableApps.comInstaller'
        if ($null -ne (Confirm-Path $eula -Directory)) {
            Test-IniWrite (Join-Path $eula 'license.ini')
            Test-DirectoryWrite $eula
        }
        # The official installer may notify a running Platform after the post-install hook.
        $platform = Join-Path $directory.Parent.FullName 'PortableApps.com'
        if ($null -ne (Confirm-Path $platform -Directory)) {
            $platformData = Join-Path $platform 'Data'
            if ($null -ne (Confirm-Path $platformData -Directory)) {
                [void](Confirm-Path (Join-Path $platformData 'NewApp.ini'))
            }
        }
    }
    if ($ValidateInstall) {
        foreach ($name in @('App', 'Other')) {
            $path = Join-Path $Root $name
            if ($null -ne (Confirm-Path $path -Directory)) { Confirm-Tree $path }
        }
        foreach ($name in @('JjokgyeolPortable.exe', 'help.html')) {
            [void](Confirm-Path (Join-Path $Root $name))
        }
        exit 0
    }
    foreach ($name in @('App', 'App/Jjokgyeol', 'App/Jjokgyeol/WebView2')) {
        $item = Get-Item -LiteralPath (Join-Path $Root $name) -Force
        Confirm-Item $item
        if ($item -isnot [IO.DirectoryInfo]) { throw "Expected a directory: $($item.FullName)" }
    }
    $webview = $item
    $appInfo = Get-Item -LiteralPath (Join-Path $Root 'App/AppInfo') -Force
    Confirm-Item $appInfo
    Confirm-Tree $appInfo.FullName
    Confirm-Item (Get-Item -LiteralPath (Join-Path $Root 'App/Jjokgyeol/jjokgyeol-desktop.exe') -Force)
    Confirm-Tree $webview.FullName
    foreach ($path in @($data, $profile)) {
        if ($null -eq (Confirm-Path $path -Directory)) { [void][IO.Directory]::CreateDirectory($path) }
    }
    Test-DirectoryWrite $profile
    foreach ($file in @('msedgewebview2.exe', 'msedge.dll')) {
        if (-not (Test-Path -LiteralPath (Join-Path $webview.FullName $file) -PathType Leaf)) {
            throw "Missing WebView2 runtime file: $file"
        }
    }
    $contents = @(Get-ChildItem -LiteralPath $webview.FullName -Recurse -Force)
    # Prepare the AppContainer ACLs required on Windows 10 and planned for
    # Windows 11 from runtime 159. This also exercises them with pinned 158.
    $sids = @('S-1-15-2-1', 'S-1-15-2-2')
    $identityType = [Security.Principal.SecurityIdentifier]
    $rx = [Security.AccessControl.FileSystemRights]::ReadAndExecute
    function Confirm-RuntimeAccess($item) {
        # The launcher's Windows PowerShell host may not load Microsoft.PowerShell.Security.
        $acl = $item.GetAccessControl([Security.AccessControl.AccessControlSections]::Access)
        $rules = $acl.GetAccessRules($true, $true, $identityType)
        foreach ($sid in $sids) {
            $matching = @($rules | Where-Object {
                $_.IdentityReference.Value -eq $sid -and $_.PropagationFlags -eq 'None'
            })
            if (@($matching | Where-Object {
                $_.AccessControlType -eq 'Deny' -and ($_.FileSystemRights -band $rx)
            }).Count) { return $false }
            $allowed = @($matching | Where-Object {
                $_.AccessControlType -eq 'Allow' -and ($_.FileSystemRights -band $rx) -eq $rx -and
                ($item.FullName -ne $webview.FullName -or [int]$_.InheritanceFlags -eq 3)
            })
            if (-not $allowed.Count) { return $false }
        }
        return $true
    }
    if (-not (Confirm-RuntimeAccess $webview)) {
        & "$env:SystemRoot\System32\icacls.exe" $webview.FullName /grant '*S-1-15-2-1:(OI)(CI)(RX)' '*S-1-15-2-2:(OI)(CI)(RX)' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Could not grant WebView2 access.' }
    }
    foreach ($item in @($webview) + $contents) {
        if (-not (Confirm-RuntimeAccess $item)) { throw "Missing WebView2 read/execute access on $($item.FullName)." }
    }
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
