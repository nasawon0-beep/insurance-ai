param(
    [Parameter(Mandatory=$true)][string]$Program,
    [string]$HostAddress = '',
    [ValidateSet('Auto','Private','Public')][string]$Scope = 'Auto',
    [ValidateSet('query','enable','disable')][string]$Operation = 'query',
    [bool]$Qa = $false
)
# Embedded by Rust; never downloaded or executed from a writable script file.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$directory = [IO.Path]::GetDirectoryName($Program).TrimEnd('\').ToLowerInvariant()
$sha = [Security.Cryptography.SHA256]::Create()
try { $id = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($directory)))).Replace('-','').ToLowerInvariant() }
finally { $sha.Dispose() }
$name = 'MoaDoom.MobileSync.v1.' + $id
$owner = 'MoaDoom.MobileSync.Install.' + $id
$description = 'MoaDoom managed mobile TLS rule v1; installation=' + $id
$profile = 'Private'
$status = [ordered]@{
    supported = $true; qa = $Qa; networkCategory = 'unavailable'; ruleState = 'unknown'
    ruleReady = $false; canApply = $false; programPath = $Program; ruleName = $name
    connectivityVerified = $false; lastError = $null; applyOutcome = $null
}
function Owned($Rule) { $Rule.Group -ceq $owner -and $Rule.Description -ceq $description }
function ExactRule($Rule) {
    $app = @($Rule | Get-NetFirewallApplicationFilter)
    $port = @($Rule | Get-NetFirewallPortFilter)
    $address = @($Rule | Get-NetFirewallAddressFilter)
    return ((Owned $Rule) -and $Rule.Enabled -eq 'True' -and $Rule.Direction -eq 'Inbound' -and
        $Rule.Action -eq 'Allow' -and [string]$Rule.Profile -eq $profile -and
        [string]$Rule.EdgeTraversalPolicy -eq 'Block' -and
        $app.Count -eq 1 -and $app[0].Program -ieq $Program -and
        $port.Count -eq 1 -and [string]$port[0].Protocol -in @('TCP','6') -and
        [string]$port[0].LocalPort -eq '18443' -and [string]$port[0].RemotePort -eq 'Any' -and
        $address.Count -eq 1 -and [string]$address[0].LocalAddress -eq 'Any' -and
        [string]$address[0].RemoteAddress -eq 'LocalSubnet')
}
try {
    # Match the interface used by mobile_sync, not an arbitrary other Private adapter.
    if ($HostAddress) {
        $addresses = @(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -eq $HostAddress })
        $categories = @($addresses | ForEach-Object {
            Get-NetConnectionProfile -InterfaceIndex $_.InterfaceIndex
        } | ForEach-Object { [string]$_.NetworkCategory } | Select-Object -Unique)
        if ($categories.Count -eq 1) {
            switch ($categories[0]) {
                'Private' { $status.networkCategory = 'private' }
                'Public' { $status.networkCategory = 'public' }
                'DomainAuthenticated' { $status.networkCategory = 'domain' }
            }
        }
    }
    if ($Scope -eq 'Public' -or ($Scope -eq 'Auto' -and $status.networkCategory -eq 'public')) {
        $profile = 'Public'
        $name = 'MoaDoom.MobileSync.Public.Allow.v1.' + $id
        $description = 'MoaDoom managed mobile TLS Public allow v1; installation=' + $id
        $status.ruleName = $name
    }
    # Separate Public identity: never adopt/delete old session tasks or other rules.
    # Enumerate then literal equality: -Name accepts wildcards, installation paths must not.
    $rules = @(Get-NetFirewallRule -PolicyStore PersistentStore | Where-Object { $_.Name -ceq $name })
    if ($rules.Count -gt 1 -or ($rules.Count -eq 1 -and -not (Owned $rules[0]))) {
        $status.ruleState = 'ownershipConflict'
        throw 'Rule identity collision; unrelated rules were left untouched.'
    }
    $status.canApply = -not $Qa -and $status.networkCategory -eq $profile.ToLowerInvariant()
    if ($Operation -ne 'query') {
        if ($Qa) { throw 'QA profiles cannot change firewall rules.' }
        if ($Operation -eq 'enable' -and $profile -eq 'Public' -and $Scope -ne 'Public') { throw 'Explicit Public permission is required.' }
        if ($Operation -eq 'enable' -and $status.networkCategory -ne $profile.ToLowerInvariant()) {
            throw 'The requested network profile is not verified. Network categories are never changed.'
        }
        if ($Operation -eq 'disable') {
            if ($rules.Count -eq 1 -and $rules[0].Enabled -ne 'False') {
                Set-NetFirewallRule -InputObject $rules[0] -Enabled False | Out-Null
            }
        } else {
            $settings = @{
                Program=$Program; Direction='Inbound'; Action='Allow'; Enabled='True'; Profile=$profile
                Protocol='TCP'; LocalPort=18443; RemotePort='Any'; LocalAddress='Any'; RemoteAddress='LocalSubnet'
                EdgeTraversalPolicy='Block'
            }
            if ($rules.Count -eq 0) {
                New-NetFirewallRule -PolicyStore PersistentStore -Name $name -DisplayName ('MoaDoom Mobile Sync ('+$profile+' subnet)') -Group $owner -Description $description @settings | Out-Null
            } elseif (-not (ExactRule $rules[0])) {
                # Same installation identity survives program updates. Never touch legacy broad/QA rules.
                Set-NetFirewallRule -InputObject $rules[0] @settings | Out-Null
            }
        }
        $status.applyOutcome = 'applied'
        $rules = @(Get-NetFirewallRule -PolicyStore PersistentStore | Where-Object { $_.Name -ceq $name })
    }
    if ($rules.Count -eq 0 -or $rules[0].Enabled -eq 'False') {
        $active = @(Get-NetFirewallRule -PolicyStore ActiveStore | Where-Object { $_.Name -ceq $name -and $_.Enabled -ne 'False' })
        if ($active.Count -ne 0) { $status.ruleState = 'ineffective' }
        elseif ($rules.Count -eq 0) { $status.ruleState = 'missing' }
        else { $status.ruleState = 'disabled' }
    }
    elseif (-not (ExactRule $rules[0])) { $status.ruleState = 'outdated' }
    else {
        $active = @(Get-NetFirewallRule -PolicyStore ActiveStore | Where-Object { $_.Name -ceq $name })
        if ($active.Count -eq 1 -and (ExactRule $active[0])) {
            $status.ruleState = 'ready'; $status.ruleReady = $true
        } else { $status.ruleState = 'ineffective' }
    }
} catch {
    $status.lastError = $_.Exception.Message
    $status.canApply = $false
    $status.ruleReady = $false
    if ($Operation -ne 'query') { $status.applyOutcome = 'failed' }
}
$status | ConvertTo-Json -Compress
# A nonzero child code prevents UAC/partial errors from looking like a successful apply.
if ($Operation -ne 'query' -and $status.applyOutcome -ne 'applied') { exit 1 }
