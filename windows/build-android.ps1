param([string]$RepositoryPath = '', [switch]$Force)
$ErrorActionPreference = 'Stop'
$repo = if ($RepositoryPath.Trim()) { (Resolve-Path -LiteralPath $RepositoryPath).Path } else { (Resolve-Path (Join-Path $PSScriptRoot '..')).Path }
$project = Join-Path $repo 'OS\android'
if (-not (Test-Path -LiteralPath (Join-Path $project 'settings.gradle.kts') -PathType Leaf)) { throw 'Android sources missing.' }
$version = (Get-Content -LiteralPath (Join-Path $repo 'VERSION') -Raw -Encoding UTF8).Trim()
if ($version -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid VERSION.' }
$git = Get-Command git.exe -ErrorAction SilentlyContinue
if (-not $git) { throw 'Git for Windows is required for Android versioning.' }
$commit = ([string](@(& $git.Source -C $repo rev-parse HEAD 2>&1) | Select-Object -First 1)).Trim()
if ($LASTEXITCODE -ne 0 -or $commit -notmatch '^[0-9a-fA-F]{40}$') { throw 'Cannot determine Git HEAD.' }
$count = ([string](@(& $git.Source -C $repo rev-list --count HEAD 2>&1) | Select-Object -First 1)).Trim()
if ($LASTEXITCODE -ne 0 -or $count -notmatch '^\d+$') { throw 'Cannot determine Android build number.' }
$buildCode = [int]$count
$root = Join-Path $repo '.autobuild'
$tools = Join-Path $root 'android-tools'
$apks = Join-Path $root 'android-apk'
New-Item -ItemType Directory -Force -Path $tools,$apks | Out-Null
$statePath = Join-Path $apks 'build-state.json'
$apk = Join-Path $apks ('Leshy-Android-' + $version + '-' + $commit.Substring(0,10).ToLowerInvariant() + '-debug.apk')
$cacheHit = $false
if (-not $Force -and (Test-Path -LiteralPath $apk -PathType Leaf) -and (Test-Path -LiteralPath $statePath -PathType Leaf)) {
    try {
        $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $cacheHit = ($state.sha -eq $commit -and $state.version -eq $version -and
            $state.sha256 -eq (Get-FileHash -LiteralPath $apk -Algorithm SHA256).Hash)
    } catch { }
}
function Write-DiskSpace {
    try {
        $drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($root))
        $free = [double]$drive.AvailableFreeSpace
        Write-Host ('ANDROID_FREE_GIB: ' + [Math]::Round($free / 1GB,2))
        return $free
    } catch { Write-Warning 'Disk space information unavailable.'; return [double]::MaxValue }
}
if (-not $cacheHit) {
    if ((Write-DiskSpace) -lt 3GB) { throw 'Android compilation requires at least 3 GiB free.' }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    function Find-Jdk {
        $candidates = @($env:JAVA_HOME)
        foreach ($base in @([Environment]::GetFolderPath('ProgramFiles'),[Environment]::GetFolderPath('ProgramFilesX86'))) {
            if (-not $base) { continue }
            $candidates += (Join-Path $base 'Android\Android Studio\jbr')
            foreach ($folder in @('Microsoft','Eclipse Adoptium')) {
                $path = Join-Path $base $folder
                if (Test-Path -LiteralPath $path -PathType Container) {
                    $candidates += @(Get-ChildItem -LiteralPath $path -Directory -ErrorAction SilentlyContinue |
                        Where-Object { $_.Name -match '(jdk|temurin)' } | ForEach-Object { $_.FullName })
                }
            }
        }
        $cmd = Get-Command javac.exe -ErrorAction SilentlyContinue
        if ($cmd) { $candidates += (Split-Path -Parent (Split-Path -Parent $cmd.Source)) }
        foreach ($jdkCandidateHome in $candidates) {
            if (-not $jdkCandidateHome) { continue }
            $java = Join-Path $jdkCandidateHome 'bin\java.exe'
            $javac = Join-Path $jdkCandidateHome 'bin\javac.exe'
            if (-not (Test-Path -LiteralPath $java -PathType Leaf) -or -not (Test-Path -LiteralPath $javac -PathType Leaf)) { continue }
            $ver = ([string](@(& $javac -version 2>$null | Select-Object -First 1))).Trim()
            if ($ver -match '^javac\s+(\d+)' -and [int]$Matches[1] -ge 17 -and [int]$Matches[1] -le 23) {
                return (Resolve-Path -LiteralPath $jdkCandidateHome).Path
            }
        }
        return $null
    }
    $jdk = Find-Jdk
    if (-not $jdk) {
        $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
        if (-not $winget) { throw 'JDK 17 missing and winget unavailable.' }
        Write-Host 'Preparing Microsoft OpenJDK 17 via winget...'
        & $winget.Source install --id Microsoft.OpenJDK.17 --exact --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { throw 'JDK 17 auto-install failed.' }
        $jdk = Find-Jdk
        if (-not $jdk) { throw 'JDK installed but not found. Reopen PowerShell and retry.' }
    }
    $env:JAVA_HOME = $jdk
    $env:Path = (Join-Path $jdk 'bin') + ';' + $env:Path
    Write-Host ('ANDROID_JDK: ' + $jdk)
    $candidates = @($env:ANDROID_HOME,$env:ANDROID_SDK_ROOT)
    if ($env:LOCALAPPDATA) { $candidates += (Join-Path $env:LOCALAPPDATA 'Android\Sdk') }
    $candidates += (Join-Path $tools 'sdk')
    $sdk = $null
    foreach ($entry in $candidates) {
        if (-not $entry -or -not (Test-Path -LiteralPath $entry -PathType Container)) { continue }
        if ((Test-Path -LiteralPath (Join-Path $entry 'cmdline-tools\latest\bin\sdkmanager.bat') -PathType Leaf) -or
            (Test-Path -LiteralPath (Join-Path $entry 'platforms\android-35') -PathType Container)) {
            $sdk = (Resolve-Path -LiteralPath $entry).Path
            break
        }
    }
    if (-not $sdk) { $sdk = Join-Path $tools 'sdk' }
    New-Item -ItemType Directory -Path $sdk -Force | Out-Null
    $manager = Join-Path $sdk 'cmdline-tools\latest\bin\sdkmanager.bat'
    if (-not (Test-Path -LiteralPath $manager -PathType Leaf)) {
        $package = 'commandlinetools-win-15859902_latest.zip'
        $expected = '90ae805d20434428bffcb699c290860f19bb5f66a67e6b330067e3de801fb04a'
        $stage = Join-Path $tools ('sdk-tmp-' + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $stage | Out-Null
        try {
            $zip = Join-Path $stage $package
            Invoke-WebRequest -Uri ('https://dl.google.com/android/repository/' + $package) -UseBasicParsing -OutFile $zip
            if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash -ine $expected) { throw 'Official Android SDK tools checksum mismatch.' }
            Expand-Archive -LiteralPath $zip -DestinationPath $stage -Force
            $src = Join-Path $stage 'cmdline-tools'
            if (-not (Test-Path -LiteralPath (Join-Path $src 'bin\sdkmanager.bat') -PathType Leaf)) { throw 'Malformed Google SDK tools archive.' }
            $parent = Join-Path $sdk 'cmdline-tools'
            New-Item -ItemType Directory -Force -Path $parent | Out-Null
            Move-Item -LiteralPath $src -Destination (Join-Path $parent 'latest') -ErrorAction Stop
        } finally {
            Remove-Item -LiteralPath $stage -Force -Recurse -ErrorAction SilentlyContinue
        }
    }
    $env:ANDROID_HOME = $sdk
    $env:ANDROID_SDK_ROOT = $sdk
    Write-Host ('ANDROID_SDK: ' + $sdk)
    # The build command explicitly requests local Android toolchain setup.
    $answers = (1..60 | ForEach-Object { 'y' }) -join [Environment]::NewLine
    $answers | & $manager ("--sdk_root=" + $sdk) --licenses | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Android SDK license acceptance failed.' }
    & $manager ("--sdk_root=" + $sdk) 'platforms;android-35' 'build-tools;35.0.0' 'platform-tools'
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $sdk 'platforms\android-35') -PathType Container)) {
        throw 'Android SDK Platform 35 preparation failed.'
    }
    $gradleVersion = '8.9'
    $gradleRoot = Join-Path $tools ('gradle-' + $gradleVersion)
    $gradle = Join-Path $gradleRoot 'bin\gradle.bat'
    if (-not (Test-Path -LiteralPath $gradle -PathType Leaf)) {
        $file = 'gradle-' + $gradleVersion + '-bin.zip'
        $url = 'https://services.gradle.org/distributions/' + $file
        $stage = Join-Path $tools ('gradle-tmp-' + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $stage | Out-Null
        try {
            $zip = Join-Path $stage $file
            $hashFile = $zip + '.sha256'
            Invoke-WebRequest -Uri $url -UseBasicParsing -OutFile $zip
            Invoke-WebRequest -Uri ($url + '.sha256') -UseBasicParsing -OutFile $hashFile
            $hash = ((Get-Content -LiteralPath $hashFile -Raw -Encoding ASCII).Trim() -split '\s+')[0]
            if ($hash -notmatch '^[0-9a-fA-F]{64}$' -or (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash -ine $hash) {
                throw 'Official Gradle distribution checksum mismatch.'
            }
            Expand-Archive -LiteralPath $zip -DestinationPath $stage -Force
            Move-Item -LiteralPath (Join-Path $stage ('gradle-' + $gradleVersion)) -Destination $gradleRoot -ErrorAction Stop
        } finally {
            Remove-Item -LiteralPath $stage -Force -Recurse -ErrorAction SilentlyContinue
        }
    }
    if (-not (Test-Path -LiteralPath $gradle -PathType Leaf)) { throw 'Gradle 8.9 not available.' }
    Push-Location -LiteralPath $project
    try {
        Write-Host ('ANDROID_COMPILING: version=' + $version + ', code=' + $buildCode)
        & $gradle --no-daemon --console=plain --parallel --build-cache ("-PleshyVersion=" + $version) ("-PleshyVersionCode=" + $buildCode) ':app:assembleDebug'
        if ($LASTEXITCODE -ne 0) { throw ('Android Gradle build failed: ' + $LASTEXITCODE) }
    } finally { Pop-Location }
    $source = Join-Path $project 'app\build\outputs\apk\debug\app-debug.apk'
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw 'Gradle reported success but APK is missing.' }
    Copy-Item -LiteralPath $source -Destination $apk -Force
    [ordered]@{
        sha = $commit
        version = $version
        version_code = $buildCode
        sha256 = (Get-FileHash -LiteralPath $apk -Algorithm SHA256).Hash
        built_at = (Get-Date).ToString('o')
    } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $statePath -Encoding UTF8
} else {
    Write-Host ('ANDROID_BUILD_CACHE_HIT: ' + $version + ' ' + $commit.Substring(0,10))
}
$releaseScript = Join-Path $PSScriptRoot 'android-release.ps1'
if (-not (Test-Path -LiteralPath $releaseScript -PathType Leaf)) { throw 'Android release helper is missing.' }
& $releaseScript -RepositoryPath $repo -ApkPath $apk -Version $version -CommitSha $commit
if (-not $?) { throw 'Android Desktop/Release stage failed.' }
Write-DiskSpace | Out-Null
Write-Host ('ANDROID_APK_READY: ' + $apk)
Write-Host 'APK installation on phone is manual. ADB install is not invoked.'
