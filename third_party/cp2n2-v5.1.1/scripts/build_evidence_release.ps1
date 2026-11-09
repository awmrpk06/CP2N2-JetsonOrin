param(
    [Parameter()]
    [ValidatePattern('^v[0-9]+\.[0-9]+(?:\.[0-9]+)?$')]
    [string]$Version = 'v5.1.1',

    [Parameter()]
    [string]$A6Run = 'evaluation/results/a6-rq2-reference-run-v5.1.0-20260804',

    [Parameter()]
    [string]$SelectorRun = 'evaluation/results/selector-reference-v5.1.1-20260811',

    [Parameter()]
    [string]$OutputRoot = 'release'
)

$ErrorActionPreference = 'Stop'

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $repositoryRoot

$branch = (& git branch --show-current).Trim()
if ($branch -ne 'main') {
    throw "Evidence releases must be built from main; current branch is '$branch'."
}

$trackedStatus = & git status --porcelain=v1 --untracked-files=no
if ($trackedStatus) {
    throw 'Evidence releases require no tracked worktree changes.'
}

$commit = (& git rev-parse HEAD).Trim()
$remoteCommit = (& git rev-parse origin/main).Trim()
if ($commit -ne $remoteCommit) {
    throw 'Local main and origin/main do not identify the same commit.'
}

$a6Directory = (Resolve-Path -LiteralPath (Join-Path $repositoryRoot $A6Run)).Path
$selectorDirectory = (Resolve-Path -LiteralPath (Join-Path $repositoryRoot $SelectorRun)).Path
$a6ManifestPath = Join-Path $a6Directory 'manifest.json'
$selectorChecksumsPath = Join-Path $selectorDirectory 'SHA256SUMS.txt'

$a6Manifest = Get-Content -LiteralPath $a6ManifestPath -Raw | ConvertFrom-Json
foreach ($entry in $a6Manifest.files.PSObject.Properties) {
    $path = Join-Path $a6Directory $entry.Name
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
    if ($actual -ne $entry.Value) {
        throw "A6 checksum mismatch: $($entry.Name)"
    }
}

foreach ($line in Get-Content -LiteralPath $selectorChecksumsPath) {
    if ($line -notmatch '^([0-9a-f]{64})  (.+)$') {
        throw "Malformed selector checksum line: $line"
    }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $selectorDirectory $Matches[2])).Hash.ToLowerInvariant()
    if ($actual -ne $Matches[1]) {
        throw "Selector checksum mismatch: $($Matches[2])"
    }
}

$requestCount = (Get-Content -LiteralPath (Join-Path $a6Directory 'raw-requests.jsonl') | Measure-Object -Line).Lines
$aggregateCells = (Get-Content -LiteralPath (Join-Path $a6Directory 'summary.csv') | Measure-Object -Line).Lines - 1
if ($requestCount -ne 945 -or $aggregateCells -ne 30) {
    throw "Unexpected A6 dimensions: requests=$requestCount, aggregate_cells=$aggregateCells"
}

$candidateName = "cp2n2-$Version"
$outputDirectory = Join-Path $repositoryRoot (Join-Path $OutputRoot $candidateName)
if (-not (Test-Path -LiteralPath $outputDirectory)) {
    New-Item -ItemType Directory -Path $outputDirectory | Out-Null
}

$archiveName = "$candidateName-evidence.zip"
$archivePath = Join-Path $outputDirectory $archiveName
if (Test-Path -LiteralPath $archivePath) {
    throw "Evidence archive already exists: $archivePath"
}

Compress-Archive -LiteralPath $a6Directory, $selectorDirectory -DestinationPath $archivePath -CompressionLevel Optimal

$archiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash.ToLowerInvariant()
$a6ManifestHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $a6ManifestPath).Hash.ToLowerInvariant()
$selectorChecksumsHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $selectorChecksumsPath).Hash.ToLowerInvariant()

$manifest = [ordered]@{
    project = 'CP2N2'
    version = $Version
    release_commit = $commit
    archive = $archiveName
    archive_sha256 = $archiveHash
    a6 = [ordered]@{
        directory = Split-Path -Leaf $a6Directory
        source_commit = $a6Manifest.git_commit
        manifest_sha256 = $a6ManifestHash
        requests = $requestCount
        aggregate_cells = $aggregateCells
    }
    selector = [ordered]@{
        directory = Split-Path -Leaf $selectorDirectory
        checksums_sha256 = $selectorChecksumsHash
        conformance_cases = 7
        holdout_cases = 4
        weight_perturbations = 32
    }
    evidence_boundary = 'A6 same-host software evidence and E3 SDK-simulator evidence; no physical CL1 or biological-performance claim'
    created_utc = [DateTime]::UtcNow.ToString('o')
}
$manifestPath = Join-Path $outputDirectory 'evidence-manifest.json'
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding utf8

$manifestHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $manifestPath).Hash.ToLowerInvariant()
$checksumPath = Join-Path $outputDirectory 'EVIDENCE-SHA256SUMS.txt'
Set-Content -LiteralPath $checksumPath -Encoding ascii -NoNewline -Value "$archiveHash  $archiveName`n$manifestHash  evidence-manifest.json`n"

Write-Output "Evidence release prepared in: $outputDirectory"
Write-Output "Commit: $commit"
Write-Output "Requests: $requestCount; aggregate cells: $aggregateCells"
Write-Output "SHA-256: $archiveHash"
