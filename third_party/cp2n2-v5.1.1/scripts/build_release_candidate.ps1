param(
    [Parameter()]
    [ValidatePattern('^v[0-9]+\.[0-9]+(?:\.[0-9]+)?$')]
    [string]$Version = 'v5.0',

    [Parameter()]
    [string]$OutputRoot = 'release'
)

$ErrorActionPreference = 'Stop'

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $repositoryRoot

$branch = (& git branch --show-current).Trim()
if ($branch -ne 'main') {
    throw "Release candidates must be built from main; current branch is '$branch'."
}

$status = & git status --porcelain=v1 --untracked-files=no
if ($status) {
    throw 'Release candidates require no tracked worktree changes.'
}

$commit = (& git rev-parse HEAD).Trim()
$remoteCommit = (& git rev-parse origin/main).Trim()
if ($commit -ne $remoteCommit) {
    throw 'Local main and origin/main do not identify the same commit.'
}

$candidateName = "cp2n2-$Version"
$outputDirectory = Join-Path $repositoryRoot (Join-Path $OutputRoot $candidateName)
if (Test-Path -LiteralPath $outputDirectory) {
    throw "Output directory already exists: $outputDirectory"
}
New-Item -ItemType Directory -Path $outputDirectory | Out-Null

$archivePath = Join-Path $outputDirectory "$candidateName-source.zip"
& git archive --format=zip "--prefix=$candidateName/" "--output=$archivePath" $commit
if ($LASTEXITCODE -ne 0) {
    throw 'git archive failed.'
}

$archiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash.ToLowerInvariant()
$checksumPath = Join-Path $outputDirectory 'SHA256SUMS.txt'
Set-Content -LiteralPath $checksumPath -Encoding ascii -NoNewline -Value "$archiveHash  $candidateName-source.zip`n"

$manifest = [ordered]@{
    project = 'CP2N2'
    version = $Version
    commit = $commit
    branch = $branch
    archive = "$candidateName-source.zip"
    archive_sha256 = $archiveHash
    evidence_boundary = 'E3 SDK simulator; no physical CL1 or biological-performance claim'
    created_utc = [DateTime]::UtcNow.ToString('o')
}
$manifestPath = Join-Path $outputDirectory 'release-manifest.json'
$manifest | ConvertTo-Json | Set-Content -LiteralPath $manifestPath -Encoding utf8

Write-Output "Release candidate prepared in: $outputDirectory"
Write-Output "Commit: $commit"
Write-Output "SHA-256: $archiveHash"
