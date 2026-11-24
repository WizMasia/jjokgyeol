$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

try {
    $architecture = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    if ($architecture -ne 'AMD64' -or [Environment]::OSVersion.Version.Build -lt 19045) {
        throw 'Requires Windows 10 22H2 or later on x64.'
    }
    $webview = Get-Item -LiteralPath (Join-Path $PSScriptRoot 'WebView2') -Force
    if ($webview.FullName -notmatch '^[A-Z]:\\') { throw 'Expected a local drive.' }
    $drive = [IO.DriveInfo]::new($webview.Root.FullName)
    if ($drive.DriveType -ne 'Fixed' -or $drive.DriveFormat -ne 'NTFS') {
        throw 'Expected a local fixed NTFS drive.'
    }
    for ($directory = $webview; $null -ne $directory; $directory = $directory.Parent) {
        if ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Reparse point in runtime path: $directory"
        }
    }
    $data = Join-Path $webview.Parent.Parent.Parent.FullName 'Data'
    $profile = Join-Path $data 'WebView2'
    foreach ($path in @($data, $profile)) {
        [IO.Directory]::CreateDirectory($path) | Out-Null
        if ([IO.File]::GetAttributes($path) -band [IO.FileAttributes]::ReparsePoint) {
            throw "Reparse point in profile path: $path"
        }
    }
    $probe = Join-Path $profile ([IO.Path]::GetRandomFileName())
    [IO.File]::Open($probe, 'CreateNew', 'Write', 'None', 4096, 'DeleteOnClose').Dispose()
    foreach ($file in @('msedgewebview2.exe', 'msedge.dll')) {
        if (-not (Test-Path -LiteralPath (Join-Path $webview.FullName $file) -PathType Leaf)) {
            throw "Missing WebView2 runtime file: $file"
        }
    }
    $contents = @(Get-ChildItem -LiteralPath $webview.FullName -Recurse -Force)
    foreach ($item in $contents) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.LinkType -eq 'HardLink') {
            throw "Linked item in runtime: $($item.FullName)"
        }
    }

    # WebView2 156+ requires these inheritable permissions on Windows 10 and 11.
    $sids = @('S-1-15-2-1', 'S-1-15-2-2')
    $identityType = [Security.Principal.SecurityIdentifier]
    $rx = [Security.AccessControl.FileSystemRights]::ReadAndExecute
    function Confirm-RuntimeAccess($item) {
        $rules = (Get-Acl -LiteralPath $item.FullName).GetAccessRules($true, $true, $identityType)
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
        & "$env:SystemRoot\System32\icacls.exe" $webview.FullName /grant '*S-1-15-2-1:(OI)(CI)(RX)' '*S-1-15-2-2:(OI)(CI)(RX)'
        if ($LASTEXITCODE -ne 0) { throw 'Could not grant WebView2 access.' }
    }
    foreach ($item in @($webview) + $contents) {
        if (-not (Confirm-RuntimeAccess $item)) { throw "Cannot verify WebView2 access: $($item.FullName)" }
    }
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
