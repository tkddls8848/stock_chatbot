[CmdletBinding()]
param([switch]$Apply)

# Default: preview. Use -Apply to remove only verified disposable artifacts.
$ErrorActionPreference = 'Stop'
# 이 스크립트는 infra/scripts/ 아래에 있다. 저장소 루트는 두 단계 위다.
$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$tracked = @(git -C $workspace ls-files)
if ($LASTEXITCODE -ne 0) { throw 'Cannot verify tracked source files.' }
$running = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^(python|python3|node|ffmpeg)(\.exe)?$' -and $_.CommandLine
} | Select-Object -ExpandProperty CommandLine)

$candidates = @()
foreach ($base in @($workspace, (Join-Path $workspace 'shorts'))) {
    $candidates += Get-ChildItem -LiteralPath $base -Force -Directory | Where-Object {
        $_.Name -like '.test-*' -or $_.Name -in @('.tmp', '.pytest_cache', '.ruff_cache', '.mypy_cache')
    }
}
# An inaccessible historical test run must not prevent cleaning its siblings.
foreach ($relative in @('.tmp', '.test-tmp', '.test-tmp/pytest-of-tkddl')) {
    $base = Join-Path $workspace $relative
    if (Test-Path -LiteralPath $base) {
        $candidates += Get-ChildItem -LiteralPath $base -Force
    }
}
# 없는 경로는 건너뛴다. ErrorActionPreference가 Stop이라 그냥 두면 정리가 통째로 멈춘다.
foreach ($relative in @('shared', 'services', 'infra', 'shorts/src', 'shorts/tests')) {
    $base = Join-Path $workspace $relative
    if (-not (Test-Path -LiteralPath $base)) { continue }
    $candidates += Get-ChildItem -LiteralPath $base -Recurse -Force -Directory |
        Where-Object { $_.Name -eq '__pycache__' }
}
foreach ($relative in @(
    'shorts/videos/consensus-demo/.thumbnails',
    'shorts/videos/consensus-demo/snapshots',
    'shorts/videos/editorial-test-20260906/qa',
    'shorts/videos/editorial-test-20260906/render-final.log'
)) {
    $path = Join-Path $workspace $relative
    if (Test-Path -LiteralPath $path) { $candidates += Get-Item -LiteralPath $path -Force }
}

$removed = 0
$skipped = 0
$bytes = 0L
foreach ($candidate in @($candidates | Sort-Object FullName -Unique)) {
    try {
        if (-not (Test-Path -LiteralPath $candidate.FullName)) { continue }
        $resolved = (Resolve-Path -LiteralPath $candidate.FullName).Path
        if (-not $resolved.StartsWith($workspace + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Target is outside the workspace.'
        }
        if ($candidate.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked target.' }
        $relative = $resolved.Substring($workspace.Length + 1).Replace('\', '/')
        if (@($tracked | Where-Object { $_ -eq $relative -or $_.StartsWith($relative + '/') }).Count) {
            throw 'Target contains tracked source files.'
        }
        if (@($running | Where-Object {
            $_.Contains($resolved) -or $_.Contains($relative) -or
            $_.Contains($relative.Replace('/', '\'))
        }).Count) { throw 'Target is referenced by an active process.' }
        $contents = @()
        if ($candidate.PSIsContainer) {
            # Stop on inaccessible descendants rather than deleting uninspected content.
            $contents = @(Get-ChildItem -LiteralPath $resolved -Recurse -Force)
        }
        if (@($contents | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count) {
            throw 'Target contains a filesystem link.'
        }
        $files = if ($candidate.PSIsContainer) {
            @($contents | Where-Object { -not $_.PSIsContainer })
        } else { @($candidate) }
        $size = ($files | Measure-Object -Property Length -Sum).Sum
        if ($Apply) {
            Remove-Item -LiteralPath $resolved -Recurse -Force
            $removed++
            Write-Output "Removed: $relative"
        } else {
            Write-Output "Would remove: $relative ($($files.Count) files)"
        }
        $bytes += $size
    } catch {
        $skipped++
        Write-Warning "Skipped $($candidate.FullName): $($_.Exception.Message)"
    }
}
Write-Output ('Verified size: {0:N2} MiB; removed targets: {1}; skipped: {2}' -f ($bytes / 1MB), $removed, $skipped)
if (-not $Apply) { Write-Output 'Preview only. Run this script with -Apply to perform cleanup.' }
