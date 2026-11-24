function Get-InstalledWebView2Version {
    # The Evergreen Runtime registers pv in either the machine or user hive.
    $client = 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    foreach ($key in @("HKLM:\SOFTWARE\WOW6432Node\$client", "HKCU:\SOFTWARE\$client")) {
        $value = Get-ItemPropertyValue -LiteralPath $key -Name pv -ErrorAction SilentlyContinue
        if ($value -and [version]$value -gt [version]'0.0.0.0') { $value }
    }
}
