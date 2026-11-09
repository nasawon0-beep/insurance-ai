# Offline contract tests: every Windows network/firewall cmdlet is replaced.
$ErrorActionPreference = 'Stop'
function Assert($Condition, $Message) { if (-not $Condition) { throw $Message } }
$script:Writes = @()
$script:Rules = @()
$script:Category = 'Private'
function Get-NetIPAddress { [pscustomobject]@{ InterfaceIndex = 7; IPAddress = '192.0.2.2' } }
function Get-NetConnectionProfile { [pscustomobject]@{ NetworkCategory = $script:Category } }
function Get-NetFirewallRule { param($PolicyStore) $script:Rules }
function Get-NetFirewallApplicationFilter { param([Parameter(ValueFromPipeline)]$InputObject) process { [pscustomobject]@{ Program = $InputObject.Program } } }
function Get-NetFirewallPortFilter { param([Parameter(ValueFromPipeline)]$InputObject) process { [pscustomobject]@{ Protocol = $InputObject.Protocol; LocalPort = $InputObject.LocalPort; RemotePort = 'Any' } } }
function Get-NetFirewallAddressFilter { param([Parameter(ValueFromPipeline)]$InputObject) process { [pscustomobject]@{ LocalAddress = $InputObject.LocalAddress; RemoteAddress = $InputObject.RemoteAddress } } }
function New-NetFirewallRule {
    param($Name,$DisplayName,$Group,$Description,$Program,$Direction,$Action,$Enabled,$Profile,$Protocol,$LocalPort,$LocalAddress,$RemoteAddress,$RemotePort,$PolicyStore,$EdgeTraversalPolicy)
    $script:Writes += 'new'
    $script:Rules += [pscustomobject]@{ Name=$Name; Group=$Group; Description=$Description; Program=$Program; Direction=$Direction; Action=$Action; Enabled=$Enabled; Profile=$Profile; Protocol=$Protocol; LocalPort=$LocalPort; LocalAddress=$LocalAddress; RemoteAddress=$RemoteAddress; EdgeTraversalPolicy=$EdgeTraversalPolicy }
}
function Set-NetFirewallRule {
    param($InputObject,$Program,$Direction,$Action,$Enabled,$Profile,$Protocol,$LocalPort,$LocalAddress,$RemoteAddress,$RemotePort,$EdgeTraversalPolicy)
    $script:Writes += 'set'
    foreach ($key in $PSBoundParameters.Keys) { if ($key -ne 'InputObject' -and $InputObject.PSObject.Properties[$key]) { $InputObject.$key = $PSBoundParameters[$key] } }
}
$source = Join-Path $PSScriptRoot 'mobile_firewall.ps1'
Assert (Test-Path -LiteralPath $source) 'Missing portable firewall implementation'
# Match Rust's embedded `& { POLICY }` invocation instead of introducing a
# second .ps1 script scope. The mocks keep Category/Rules/Writes in this file's
# script scope; a child file invocation can bind those references elsewhere.
# This is a harness-only correction; no real NetSecurity cmdlet is invoked.
$policy = [scriptblock]::Create((Get-Content -LiteralPath $source -Raw))
function Invoke-TestPolicy($Operation='query', $Qa=$false, $Scope='Auto') {
    & $policy -Program "C:\Program Files\Moa'Doom\local-engine.exe" -HostAddress '192.0.2.2' -Operation $Operation -Qa $Qa -Scope $Scope | ConvertFrom-Json
}
$s = Invoke-TestPolicy
Assert ($null -ne $s -and $null -eq $s.lastError) 'Initial query must return a status without a policy exception'
Assert ($s.networkCategory -eq 'private') ("Private network classified; actual status: " + ($s | ConvertTo-Json -Compress))
Assert ($s.ruleState -eq 'missing' -and -not $s.ruleReady) 'Missing rule not ready'
Assert ($script:Writes.Count -eq 0) 'Query must never write'
$s = Invoke-TestPolicy 'enable'
Assert ($script:Writes.Count -eq 1 -and $script:Writes[0] -eq 'new') 'Create exactly one scoped rule'
Assert ($s.ruleReady -and -not $s.connectivityVerified) 'Rule ready never proves phone connectivity'
$r = $script:Rules[0]
Assert ($r.Profile -eq 'Private' -and $r.Protocol -eq 'TCP' -and $r.LocalPort -eq 18443) 'Private TCP18443 only'
Assert ($r.LocalAddress -eq 'Any' -and $r.RemoteAddress -eq 'LocalSubnet') 'Portable subnet scope'
Assert ($r.Program -eq "C:\Program Files\Moa'Doom\local-engine.exe") 'Exact program including spaces/apostrophe'
$s = Invoke-TestPolicy 'enable'
Assert ($script:Rules.Count -eq 1 -and $script:Writes.Count -eq 1) 'Repeated enable is a no-op'
$script:Rules[0].Program = 'C:\Old Build\local-engine.exe'
$s = Invoke-TestPolicy
Assert ($s.ruleState -eq 'outdated' -and -not $s.ruleReady) 'Old owned program is not ready'
$s = Invoke-TestPolicy 'enable'
Assert ($script:Rules.Count -eq 1 -and $script:Writes.Count -eq 2) 'Update owned stale program in place'
Assert $s.ruleReady 'Updated program is ready'
$before = $script:Writes.Count
$script:Category = 'Public'
$s = Invoke-TestPolicy
Assert ($s.networkCategory -eq 'public' -and $s.canApply -and -not $s.ruleReady) 'Public query is separate from Private and consent'
Assert ($script:Writes.Count -eq $before) 'Public query has no mutations'
$s = Invoke-TestPolicy 'enable' $false 'Public'
Assert ($s.ruleReady -and $script:Rules.Count -eq 2) 'Public enable creates separate owned rule'
Assert ($script:Rules[0].Profile -eq 'Private' -and $script:Rules[1].Profile -eq 'Public') 'Private rule preserved'
$s = Invoke-TestPolicy 'enable' $false 'Public'
Assert ($script:Rules.Count -eq 2) 'Public enable is idempotent'
$script:Category = 'Private'
$s = Invoke-TestPolicy 'disable' $false 'Public'
Assert ($s.ruleState -eq 'disabled' -and $script:Rules[0].Enabled -eq 'True') 'Public can be disabled after network change without touching Private'
$script:Category = 'Unknown'
$s = Invoke-TestPolicy
Assert ($s.networkCategory -eq 'unavailable' -and -not $s.canApply) 'Unavailable profile fails closed'
$script:Category = 'Private'
$s = Invoke-TestPolicy 'query' $true
Assert ($s.qa -and -not $s.canApply) 'QA query reports mutation prohibition'
$s = Invoke-TestPolicy 'disable'
Assert ($s.ruleState -eq 'disabled' -and -not $s.ruleReady) 'Disable only owned rule'
$before = $script:Writes.Count
$s = Invoke-TestPolicy 'disable'
Assert ($script:Writes.Count -eq $before) 'Repeated disable is a no-op'
$script:Rules[0].Group = 'Unrelated QA rule'
$s = Invoke-TestPolicy
Assert ($s.ruleState -eq 'ownershipConflict' -and -not $s.canApply -and -not $s.ruleReady) 'Ownership conflict fails closed'
Assert ($script:Writes.Count -eq $before) 'Unrelated or QA rule untouched'
Write-Output 'PASS: query, scopes, exact program, idempotence, stale program, profiles, QA, disable, ownership (offline mocks)'
