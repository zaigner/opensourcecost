<#
.SYNOPSIS
  Estimates the "true cost" (unit cost) of an open source Python package using the
  Total Cost of Stewardship (TCS) framework.

.DESCRIPTION
  Collects public data (PyPI, pypistats.org, deps.dev, GitHub) for a package and computes:
    1. Replacement Value (RV)        - COCOMO II cost to rebuild the code + tests from scratch (asset value)
    2. Annual Sustaining Cost (ASC)  - triangulated from three methods:
         a. Activity-based (observed last-12-month workload x standard times)
         b. COCOMO maintenance (Annual Change Traffic x development effort)
         c. Responsible-staffing standard (tier-based minimum staffing for bus factor >= 2)
    3. Downstream Remediation Cost (DRC) - cost pushed onto direct dependents by each advisory
    4. Unit costs and fair-share allocations (per download, per dependent, per developer, per enterprise)

  Set $env:GITHUB_TOKEN to avoid the 60 request/hour unauthenticated GitHub limit.

.EXAMPLE
  .\Get-OssUnitCost.ps1 -Package urllib3 -AnnualFunding 15525
#>
param(
  [Parameter(Mandatory)] [string]$Package,
  [string]$Repo,                                   # owner/name; auto-detected from PyPI if omitted
  [double]$MedianWage = 133080,                    # BLS OOH, software developers, median annual wage (May 2024)
  [double]$WageShareOfComp = 0.701,                # BLS ECEC Dec 2025: wages = 70.1% of private-industry compensation
  [double]$EffortMultiplier = 1.287,               # COCOMO II EAF: RELY high (1.10) x CPLX high (1.17)
  [double]$AnnualFunding = -1,                     # known annual funding received (optional)
  [double]$CommercialShare = 0.8,                  # alpha: share of ASC attributed to commercial beneficiaries
  [double]$PythonDevelopers = 23000000,            # SlashData Q1 2025 Python developer population
  [int]$EnterprisePool = 2000,                     # Forbes Global 2000 as the anchor beneficiary pool
  [double]$RemediationHoursPerDependent = 1.0,     # hours each direct dependent spends per advisory
  [string[]]$TestDirNames = @('test','tests','testing','dummyserver'),
  [string[]]$ExcludeDirNames = @('docs','doc','examples','example','benchmarks','bench','ci','.github','scripts','tools','.git'),
  [string]$OutDir
)
$ErrorActionPreference = 'Stop'
if (-not $OutDir) { $OutDir = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'results' }
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# ---------- standard times (hours) for activity-based costing ----------
$Std = [ordered]@{
  MergedPR          = 2.5   # review, CI iteration, merge
  UnmergedPR        = 0.75  # review / close / redirect
  IssueTriage       = 1.5   # reproduce, label, respond
  Release           = 8     # changelog, verification, publish, backports
  SecurityAdvisory  = 40    # intake, fix, test, coordinated disclosure, CVE, release
  MaintainerCommit  = 4     # maintainer-authored engineering work (features, refactors)
  WeeklyUpkeep      = 4     # CI, dependency bumps, new Python versions, deprecations (per week)
  WeeklyCommunity   = 2     # docs, governance, mentoring, discussions (per week)
}
# ---------- responsible-staffing tiers ----------
$Tiers = @(
  [pscustomobject]@{ Name='Critical';  Fte=1.25; Reserve=20000; Rule='>=100M downloads/month OR >=1,000 direct dependents' },
  [pscustomobject]@{ Name='Important'; Fte=0.60; Reserve=7500;  Rule='>=5M downloads/month OR >=100 direct dependents' },
  [pscustomobject]@{ Name='Standard';  Fte=0.15; Reserve=2000;  Rule='all other packages' }
)

$H = @{ 'User-Agent' = 'oss-unit-cost-research'; 'Accept' = 'application/vnd.github+json' }
if ($env:GITHUB_TOKEN) { $H['Authorization'] = "Bearer $env:GITHUB_TOKEN" }
function Get-Json($url) { Invoke-RestMethod $url -Headers $H }
function Get-JsonArray($url) { $r = Invoke-RestMethod $url -Headers $H; foreach ($x in $r) { $x } }  # unroll PS 5.1 array wrapping
function Write-Step($m) { Write-Host "  - $m" -ForegroundColor DarkGray }

$loadedAnnual = $MedianWage / $WageShareOfComp
$hourly = $loadedAnnual / 1824          # 152 h/person-month x 12 (COCOMO II convention)
$perPM  = $loadedAnnual / 12
$since  = (Get-Date).AddYears(-1)
$sinceS = $since.ToString('yyyy-MM-dd')

Write-Host "Collecting data for $Package ..." -ForegroundColor Cyan

# ---------- PyPI ----------
Write-Step 'PyPI metadata'
$pj = Get-Json "https://pypi.org/pypi/$Package/json"
$version = $pj.info.version
$releases12 = @($pj.releases.PSObject.Properties | Where-Object { $_.Value.Count -gt 0 -and [datetime]$_.Value[0].upload_time -ge $since }).Count
if (-not $Repo) {
  $urls = @()
  if ($pj.info.project_urls) { $urls += $pj.info.project_urls.PSObject.Properties.Value }
  if ($pj.info.home_page) { $urls += $pj.info.home_page }
  foreach ($u in $urls) { if ($u -match 'github\.com/([^/]+)/([^/#?]+)') { $Repo = "$($Matches[1])/$($Matches[2] -replace '\.git$','')"; break } }
  if (-not $Repo) { throw "Could not detect a GitHub repo for $Package; pass -Repo owner/name" }
}

Write-Step 'pypistats downloads'
$dl = (Get-Json "https://pypistats.org/api/packages/$($Package.ToLower())/recent").data
$monthlyDownloads = [double]$dl.last_month

Write-Step 'deps.dev dependents'
$directDeps = 0; $allDeps = 0
try { $dd = Get-Json "https://api.deps.dev/v3alpha/systems/pypi/packages/$Package/versions/$($version):dependents"
      $directDeps = [int]$dd.directDependentCount; $allDeps = [int]$dd.dependentCount } catch { Write-Warning "deps.dev: $_" }

# ---------- GitHub ----------
Write-Step "GitHub repo $Repo"
$gr = Get-Json "https://api.github.com/repos/$Repo"
$commits = @(); $page = 1
do { $b = @(Get-JsonArray "https://api.github.com/repos/$Repo/commits?since=$sinceS&per_page=100&page=$page"); $commits += $b; $page++ } while ($b.Count -eq 100 -and $page -le 10)
$human = @($commits | Where-Object { -not ($_.author -and $_.author.login -match '\[bot\]$') -and $_.commit.author.name -notmatch '\[bot\]' })
$byAuthor = @($human | ForEach-Object { if ($_.author) { $_.author.login } else { $_.commit.author.name } } | Group-Object | Sort-Object Count -Descending)
# bus-factor proxy: fewest authors accounting for >=50% of human commits
$acc = 0; $busFactor = 0
foreach ($g in $byAuthor) { $acc += $g.Count; $busFactor++; if ($acc -ge 0.5 * $human.Count) { break } }
# "maintainer" commits: authors with >= 5% of human commits
$maintainerCommits = ($byAuthor | Where-Object { $_.Count -ge [Math]::Max(3, 0.05 * $human.Count) } | Measure-Object Count -Sum).Sum
if (-not $maintainerCommits) { $maintainerCommits = 0 }

function Search-Count($q) { (Get-Json "https://api.github.com/search/issues?q=$q&per_page=1").total_count }
$prOpened  = Search-Count "repo:$Repo+is:pr+created:>=$sinceS"
$prMerged  = Search-Count "repo:$Repo+is:pr+is:merged+merged:>=$sinceS"
$issOpened = Search-Count "repo:$Repo+is:issue+created:>=$sinceS"
$issClosed = Search-Count "repo:$Repo+is:issue+closed:>=$sinceS"

Write-Step 'security advisories'
$adv = @(Get-JsonArray "https://api.github.com/advisories?ecosystem=pip&affects=$Package&per_page=100")
$adv12 = @($adv | Where-Object { [datetime]$_.published_at -ge $since })

Write-Step 'code churn (code_frequency)'
$added12 = 0; $deleted12 = 0
for ($i = 0; $i -lt 6; $i++) {
  $r = Invoke-WebRequest "https://api.github.com/repos/$Repo/stats/code_frequency" -Headers $H -UseBasicParsing
  if ($r.StatusCode -eq 200 -and $r.Content.Length -gt 5) { break }; Start-Sleep 5 }
$cut = [DateTimeOffset]::new($since).ToUnixTimeSeconds()
foreach ($w in ($r.Content | ConvertFrom-Json)) { if ($w[0] -ge $cut) { $added12 += $w[1]; $deleted12 += -1 * $w[2] } }

# ---------- source size ----------
Write-Step 'downloading source & counting lines'
$work = Join-Path ([IO.Path]::GetTempPath()) ("osscost_" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force $work | Out-Null
$zip = Join-Path $work 'src.zip'; $got = $false
foreach ($ref in @("refs/tags/$version", "refs/tags/v$version", "refs/heads/$($gr.default_branch)")) {
  try { Invoke-WebRequest "https://codeload.github.com/$Repo/zip/$ref" -OutFile $zip -UseBasicParsing -Headers @{ 'User-Agent'='oss-unit-cost-research' }; $got = $true; $srcRef = $ref; break } catch {}
}
if (-not $got) { throw 'Could not download source archive' }
Expand-Archive $zip -DestinationPath (Join-Path $work 'x') -Force
$root = (Get-ChildItem (Join-Path $work 'x') -Directory)[0].FullName

$codeExt = @('.py','.pyx','.pxd','.pyi','.c','.h','.cc','.cpp','.hpp','.rs')
$textExt = $codeExt + @('.rst','.md','.txt','.toml','.cfg','.ini','.yml','.yaml','.json','.html','.css','.js','.in','.sh')
$prod = 0; $test = 0; $physical = 0
Get-ChildItem $root -Recurse -File | ForEach-Object {
  $ext = $_.Extension.ToLower()
  if ($textExt -notcontains $ext -or $_.Length -gt 2MB) { return }
  $lines = [IO.File]::ReadAllLines($_.FullName); $physical += $lines.Count
  if ($codeExt -notcontains $ext) { return }
  $rel = $_.FullName.Substring($root.Length).ToLower()
  $segs = $rel.Split([char[]]'\/', [StringSplitOptions]::RemoveEmptyEntries)
  $dirs = if ($segs.Count -gt 1) { $segs[0..($segs.Count-2)] } else { @() }
  if (@($dirs | Where-Object { $ExcludeDirNames -contains $_ }).Count) { return }
  if ($segs.Count -eq 1 -and $_.Name -match '^(setup|noxfile|tasks|conftest)\.py$') { return }
  $isTest = @($dirs | Where-Object { $TestDirNames -contains $_ }).Count -gt 0 -or $_.Name -match '^test_.*\.py$|_test\.py$'
  $n = 0; $inDoc = $false; $isPy = $ext -like '.py*'
  foreach ($l in $lines) { $t = $l.Trim()
    if ($t -eq '') { continue }
    if ($isPy) {
      if ($inDoc) { if ($t -match '"""|''''''') { $inDoc = $false }; continue }
      if ($t -match '^("""|'''''')') { if (-not ($t.Length -gt 3 -and $t.Substring(3) -match '"""|''''''')) { $inDoc = $true }; continue }
      if ($t.StartsWith('#')) { continue }
    } elseif ($t -match '^(//|/\*|\*)') { continue }
    $n++ }
  if ($isTest) { $test += $n } else { $prod += $n }
}
Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue

# ---------- 1. Replacement value (COCOMO II post-architecture) ----------
$E = 0.91 + 0.01 * 18.97                       # nominal scale factors
$ksloc = ($prod + $test) / 1000
$pmDev = 2.94 * [Math]::Pow($ksloc, $E) * $EffortMultiplier
$rv = $pmDev * $perPM
$pmDevProdOnly = 2.94 * [Math]::Pow($prod / 1000, $E) * $EffortMultiplier

# ---------- 2a. Activity-based sustaining cost ----------
$unmerged = [Math]::Max(0, $prOpened - $prMerged)
$act = [ordered]@{
  'PR review (merged)'        = $prMerged * $Std.MergedPR
  'PR review (not merged)'    = $unmerged * $Std.UnmergedPR
  'Issue triage'              = $issOpened * $Std.IssueTriage
  'Releases'                  = $releases12 * $Std.Release
  'Security advisories'       = $adv12.Count * $Std.SecurityAdvisory
  'Maintainer engineering'    = $maintainerCommits * $Std.MaintainerCommit
  'Upkeep (CI, deps, Python)' = 52 * $Std.WeeklyUpkeep
  'Community & governance'    = 52 * $Std.WeeklyCommunity
}
$actHours = ($act.Values | Measure-Object -Sum).Sum
$ascActivity = $actHours * $hourly

# ---------- 2b. COCOMO maintenance (Annual Change Traffic) ----------
$actRatio = if ($physical) { [Math]::Min([double]1.0, [double]$added12 / $physical) } else { 0 }
$pmMaint = $actRatio * $pmDev
$ascCocomo = $pmMaint * $perPM

# ---------- 2c. Responsible-staffing standard ----------
$tier = if ($monthlyDownloads -ge 1e8 -or $directDeps -ge 1000) { $Tiers[0] } elseif ($monthlyDownloads -ge 5e6 -or $directDeps -ge 100) { $Tiers[1] } else { $Tiers[2] }
$ascStandard = $tier.Fte * $loadedAnnual + $tier.Reserve

$three = @($ascActivity, $ascCocomo, $ascStandard) | Sort-Object
$asc = $three[1]   # median of the three methods = point estimate

# ---------- 3. Downstream remediation cost ----------
$drc = $adv12.Count * $directDeps * $RemediationHoursPerDependent * $hourly

# ---------- 4. Unit costs & allocation ----------
$annualDownloads = $monthlyDownloads * 12
$result = [ordered]@{
  package = $Package; version = $version; repo = $Repo; source_ref = $srcRef; collected = (Get-Date).ToString('s')
  inputs = [ordered]@{
    monthly_downloads = $monthlyDownloads; direct_dependents = $directDeps; all_dependents = $allDeps
    stars = $gr.stargazers_count; forks = $gr.forks_count; open_issues_and_prs = $gr.open_issues_count
    commits_12mo = $commits.Count; human_commits_12mo = $human.Count; distinct_human_authors_12mo = $byAuthor.Count
    top_authors_12mo = @($byAuthor | Select-Object -First 5 | ForEach-Object { "$($_.Name)=$($_.Count)" })
    bus_factor_proxy = $busFactor; maintainer_commits_12mo = $maintainerCommits
    prs_opened_12mo = $prOpened; prs_merged_12mo = $prMerged; issues_opened_12mo = $issOpened; issues_closed_12mo = $issClosed
    releases_12mo = $releases12; advisories_total = $adv.Count; advisories_12mo = $adv12.Count
    advisories_12mo_high_or_critical = @($adv12 | Where-Object { $_.severity -in 'high','critical' }).Count
    lines_added_12mo = $added12; lines_deleted_12mo = $deleted12
    product_sloc = $prod; test_sloc = $test; physical_lines_all_text = $physical
    annual_funding_known = $AnnualFunding
  }
  rates = [ordered]@{ loaded_annual = [Math]::Round($loadedAnnual); hourly = [Math]::Round($hourly, 2); per_person_month = [Math]::Round($perPM) }
  replacement_value = [ordered]@{ ksloc = [Math]::Round($ksloc, 2); exponent = $E; effort_multiplier = $EffortMultiplier
    person_months = [Math]::Round($pmDev, 1); cost = [Math]::Round($rv)
    product_only_person_months = [Math]::Round($pmDevProdOnly, 1); product_only_cost = [Math]::Round($pmDevProdOnly * $perPM) }
  sustaining = [ordered]@{
    activity_hours = [Math]::Round($actHours); activity_breakdown_hours = $act; activity_fte = [Math]::Round($actHours / 1824, 2); activity_cost = [Math]::Round($ascActivity)
    annual_change_traffic = [Math]::Round($actRatio, 3); cocomo_maint_person_months = [Math]::Round($pmMaint, 1); cocomo_fte = [Math]::Round($pmMaint / 12, 2); cocomo_cost = [Math]::Round($ascCocomo)
    tier = $tier.Name; tier_rule = $tier.Rule; tier_fte = $tier.Fte; tier_reserve = $tier.Reserve; standard_cost = [Math]::Round($ascStandard)
    asc_low = [Math]::Round($three[0]); asc_point = [Math]::Round($asc); asc_high = [Math]::Round($three[2])
  }
  downstream = [ordered]@{ hours_per_dependent_per_advisory = $RemediationHoursPerDependent; annual_cost = [Math]::Round($drc)
    leverage_ratio = if ($asc) { [Math]::Round($drc / $asc, 1) } else { $null } }
  unit_costs = [ordered]@{
    per_million_downloads = [Math]::Round($asc / ($annualDownloads / 1e6), 2)
    per_direct_dependent_per_year = if ($directDeps) { [Math]::Round($asc / $directDeps, 2) } else { $null }
    commercial_share = $CommercialShare
    per_global2000_enterprise_per_year = [Math]::Round($CommercialShare * $asc / $EnterprisePool, 2)
    per_python_developer_per_year = [Math]::Round($CommercialShare * $asc / $PythonDevelopers, 4)
  }
  funding = if ($AnnualFunding -ge 0) { [ordered]@{ annual_funding = $AnnualFunding; coverage_ratio = [Math]::Round($AnnualFunding / $asc, 3); annual_gap = [Math]::Round($asc - $AnnualFunding) } } else { $null }
}

New-Item -ItemType Directory -Force $OutDir | Out-Null
$out = Join-Path $OutDir "$Package.json"
$result | ConvertTo-Json -Depth 6 | Out-File $out -Encoding utf8

$f = { param($v) '{0:C0}' -f $v }
Write-Host ""
Write-Host "=== Total Cost of Stewardship: $Package $version ===" -ForegroundColor Green
Write-Host ("Replacement value (rebuild)     : {0}  ({1} person-months, {2} KSLOC incl. tests)" -f (& $f $rv), [Math]::Round($pmDev,1), [Math]::Round($ksloc,1))
Write-Host ("Sustaining cost - activity      : {0}  ({1} h, {2} FTE)" -f (& $f $ascActivity), [Math]::Round($actHours), [Math]::Round($actHours/1824,2))
Write-Host ("Sustaining cost - COCOMO ACT    : {0}  (ACT {1:P1})" -f (& $f $ascCocomo), $actRatio)
Write-Host ("Sustaining cost - {0,-9} tier  : {1}" -f $tier.Name, (& $f $ascStandard))
Write-Host ("ANNUAL SUSTAINING COST (median) : {0}  (range {1} - {2})" -f (& $f $asc), (& $f $three[0]), (& $f $three[2])) -ForegroundColor Yellow
Write-Host ("Downstream remediation cost     : {0}/yr  ({1}x the sustaining cost)" -f (& $f $drc), $result.downstream.leverage_ratio)
Write-Host ("Per million downloads           : {0:C2}" -f $result.unit_costs.per_million_downloads)
Write-Host ("Per Global-2000 enterprise / yr : {0:C2}" -f $result.unit_costs.per_global2000_enterprise_per_year)
if ($result.funding) { Write-Host ("Funding coverage                : {0:P1}  (gap {1}/yr)" -f $result.funding.coverage_ratio, (& $f $result.funding.annual_gap)) }
Write-Host "Saved: $out"
