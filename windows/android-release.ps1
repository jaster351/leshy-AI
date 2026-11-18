param(
    [Parameter(Mandatory=$true)][string]$RepositoryPath,
    [Parameter(Mandatory=$true)][string]$ApkPath,
    [Parameter(Mandatory=$true)][string]$Version,
    [Parameter(Mandatory=$true)][string]$CommitSha,
    [int]$Keep = 3
)
$ErrorActionPreference = 'Stop'
if ($Keep -lt 2 -or $Keep -gt 20) { throw 'Keep must be between 2 and 20.' }
if (-not (Test-Path -LiteralPath $ApkPath -PathType Leaf)) { throw 'Cannot publish a missing APK.' }
if ($Version -notmatch '^\d+\.\d+\.\d+$' -or $CommitSha -notmatch '^[0-9a-fA-F]{40}$') {
    throw 'Invalid Android version or source commit.'
}
$repo = (Resolve-Path -LiteralPath $RepositoryPath).Path
$apkRoot = Join-Path $repo '.autobuild\android-apk'
$desktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
if (-not $desktop -or -not (Test-Path -LiteralPath $desktop -PathType Container)) {
    $desktop = Join-Path $env:USERPROFILE 'Desktop'
}
if (-not (Test-Path -LiteralPath $desktop -PathType Container)) {
    throw ('Desktop folder not found: ' + $desktop)
}
$short = $CommitSha.Substring(0,10).ToLowerInvariant()
$name = 'Leshy-Android-' + $Version + '-' + $short + '-debug.apk'
$localCopy = Join-Path $apkRoot $name
$desktopCopy = Join-Path $desktop $name
$latestDesktop = Join-Path $desktop 'Leshy-Android-current.apk'
$sha = (Get-FileHash -LiteralPath $ApkPath -Algorithm SHA256).Hash
try {
    $desktopDrive = New-Object IO.DriveInfo([IO.Path]::GetPathRoot($desktop))
    Write-Host ('ANDROID_DESKTOP_FREE_GIB: ' + [Math]::Round([double]$desktopDrive.AvailableFreeSpace / 1GB, 2))
    $apkBytes = (Get-Item -LiteralPath $ApkPath).Length
    if ($desktopDrive.AvailableFreeSpace -lt 2 * $apkBytes) {
        throw 'Insufficient free space on Desktop volume for Android APK copies.'
    }
} catch [System.IO.IOException] {
    throw
} catch {
    Write-Warning ('Cannot read Desktop volume capacity: ' + $_.Exception.Message)
}

foreach ($target in @($localCopy,$desktopCopy,$latestDesktop)) {
    if ([IO.Path]::GetFullPath($target) -ine [IO.Path]::GetFullPath($ApkPath)) {
        Copy-Item -LiteralPath $ApkPath -Destination $target -Force
    }
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ine $sha) {
        throw ('APK copy verification failed: ' + $target)
    }
}
Write-Host ('ANDROID_DESKTOP: ' + $desktopCopy)
Write-Host ('ANDROID_DESKTOP_LATEST: ' + $latestDesktop)
Write-Host ('ANDROID_SHA256: ' + $sha)

function Remove-OldAndroidCopies {
    param([string]$Directory,[string]$CurrentFile,[int]$Maximum)
    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) { return }
    $all = @(Get-ChildItem -LiteralPath $Directory -File -Filter 'Leshy-Android-*-debug.apk' |
        Where-Object { $_.Name -match '^Leshy-Android-\d+\.\d+\.\d+-[0-9a-fA-F]{10}-debug\.apk$' })
    $ordered = @($all | Sort-Object LastWriteTimeUtc -Descending)
    $keepNames = @($ordered | Select-Object -First $Maximum | ForEach-Object { $_.FullName })
    $keepNames += [IO.Path]::GetFullPath($CurrentFile)
    foreach ($item in $all) {
        if ($item.FullName -in $keepNames) { continue }
        try {
            Remove-Item -LiteralPath $item.FullName -Force -ErrorAction Stop
            Write-Host ('ANDROID_CLEANUP: ' + $item.FullName)
        } catch {
            Write-Warning ('Could not remove old APK ' + $item.Name + ': ' + $_.Exception.Message)
        }
    }
}
Remove-OldAndroidCopies -Directory $apkRoot -CurrentFile $localCopy -Maximum $Keep
Remove-OldAndroidCopies -Directory $desktop -CurrentFile $desktopCopy -Maximum $Keep

# A GitHub release is optional when no authenticated CLI is available.
# No GitHub releases unrelated to this Android app will ever be deleted.
$gh = Get-Command gh.exe -ErrorAction SilentlyContinue
if (-not $gh) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host 'Installing GitHub CLI for automatic Android Releases...'
        & $winget.Source install --id GitHub.cli --exact --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { Write-Warning 'GitHub CLI installation did not succeed.' }
        $gh = Get-Command gh.exe -ErrorAction SilentlyContinue
        if (-not $gh) {
            $candidate = Join-Path $env:ProgramFiles 'GitHub CLI\gh.exe'
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { $gh = [pscustomobject]@{ Source=$candidate } }
        }
    }
}
if (-not $gh) {
    Write-Warning 'GitHub CLI unavailable; APK kept on Desktop and local disk. Publication pending.'
    return
}
$releaseRepo = 'jaster351/leshy-AI'
$tag = 'android-v' + $Version + '-' + $short
& $gh.Source auth status 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Warning 'GitHub CLI needs authentication (gh auth login or GH_TOKEN). Desktop APK is ready; publication pending.'
    return
}
$notesFile = Join-Path $apkRoot ('release-notes-' + $short + '.md')
$git = Get-Command git.exe -ErrorAction SilentlyContinue
$changes = @()
if ($git) {
    $changes = @(& $git.Source -C $repo log -8 --pretty=format:'- %h %s' -- OS/android windows/build-android.ps1 windows/android-release.ps1)
    if ($LASTEXITCODE -ne 0) { $changes = @() }
}
$notes = @(
    '# Leshy Android client'
    ''
    ('Version: ' + $Version)
    ('Source commit: ' + $CommitSha)
    ('SHA-256: ' + $sha)
    ''
    'Experimental debug APK. Android 14+; no guarantee of a working voice dialog or production-ready face recognition.'
    'The owner installs the APK manually. No ADB installation is performed.'
    ''
    '## Changes in Android build source'
) + $changes
$notes | Set-Content -LiteralPath $notesFile -Encoding UTF8
try {
    & $gh.Source release view $tag --repo $releaseRepo --json tagName --jq .tagName 2>$null | Out-Null
    $found = ($LASTEXITCODE -eq 0)
    if ($found) {
        & $gh.Source release upload $tag $desktopCopy --repo $releaseRepo --clobber
    } else {
        & $gh.Source release create $tag $desktopCopy --repo $releaseRepo --title ('Leshy Android ' + $Version + ' (' + $short + ')') --notes-file $notesFile --prerelease --target $CommitSha
    }
    if ($LASTEXITCODE -ne 0) { throw 'GitHub release upload/create failed.' }
    $remote = & $gh.Source release view $tag --repo $releaseRepo --json tagName,assets | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not (@($remote.assets) | Where-Object { $_.name -eq $name })) {
        throw 'GitHub Release does not report the expected APK asset. Old releases will not be removed.'
    }
    Write-Host ('ANDROID_GITHUB_RELEASE: https://github.com/' + $releaseRepo + '/releases/tag/' + $tag)
    # Prune only automatic releases with the fixed Android tag pattern.
    $raw = & $gh.Source release list --repo $releaseRepo --limit 500 --json tagName,createdAt,isPrerelease
    if ($LASTEXITCODE -ne 0) { throw 'Could not list old Android releases. No remote cleanup performed.' }
    $allReleases = @($raw | ConvertFrom-Json | Where-Object {
        $_.tagName -match '^android-v\d+\.\d+\.\d+-[0-9a-fA-F]{10}
    $keepTags = @($allReleases | Select-Object -First $Keep | ForEach-Object { $_.tagName })
    $keepTags += $tag
    foreach ($release in $allReleases) {
        if ($release.tagName -in $keepTags) { continue }
        & $gh.Source release delete $release.tagName --repo $releaseRepo --yes --cleanup-tag
        if ($LASTEXITCODE -eq 0) { Write-Host ('ANDROID_REMOTE_CLEANUP: ' + $release.tagName) }
        else { Write-Warning ('Could not delete old Android release ' + $release.tagName) }
    }
} catch {
    Write-Warning ('GitHub publication or cleanup incomplete: ' + $_.Exception.Message)
    Write-Warning 'Desktop APK retained. Release publication will be retried on the next build invocation.'
} finally {
    Remove-Item -LiteralPath $notesFile -Force -ErrorAction SilentlyContinue
}
 -and $_.isPrerelease -eq $true
    } | Sort-Object createdAt -Descending)
    $keepTags = @($allReleases | Select-Object -First $Keep | ForEach-Object { $_.tagName })
    $keepTags += $tag
    foreach ($release in $allReleases) {
        if ($release.tagName -in $keepTags) { continue }
        & $gh.Source release delete $release.tagName --repo $releaseRepo --yes --cleanup-tag
        if ($LASTEXITCODE -eq 0) { Write-Host ('ANDROID_REMOTE_CLEANUP: ' + $release.tagName) }
        else { Write-Warning ('Could not delete old Android release ' + $release.tagName) }
    }
} catch {
    Write-Warning ('GitHub publication or cleanup incomplete: ' + $_.Exception.Message)
    Write-Warning 'Desktop APK retained. Release publication will be retried on the next build invocation.'
} finally {
    Remove-Item -LiteralPath $notesFile -Force -ErrorAction SilentlyContinue
}
