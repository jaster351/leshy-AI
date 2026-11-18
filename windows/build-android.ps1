param([string]$RepositoryPath = '')
$ErrorActionPreference = 'Stop'
$repo = if ($RepositoryPath.Trim()) { (Resolve-Path -LiteralPath $RepositoryPath).Path } else { (Resolve-Path (Join-Path $PSScriptRoot '..')).Path }
$project = Join-Path $repo 'OS\android'
if (-not (Test-Path -LiteralPath (Join-Path $project 'settings.gradle.kts') -PathType Leaf)) {
    throw "Android project missing in selected branch: $project"
}
$root = Join-Path $repo '.autobuild'
$toolsRoot = Join-Path $root 'android-tools'
$apkRoot = Join-Path $root 'android-apk'
New-Item -ItemType Directory -Force -Path $toolsRoot, $apkRoot | Out-Null

# Android Studio SDK or a user-specified SDK. Avoid editing local.properties.
$possibleSdk = New-Object 'System.Collections.Generic.List[string]'
foreach ($value in @($env:ANDROID_HOME, $env:ANDROID_SDK_ROOT)) {
    if ($value) { $possibleSdk.Add($value) }
}
if ($env:LOCALAPPDATA) { $possibleSdk.Add((Join-Path $env:LOCALAPPDATA 'Android\Sdk')) }
$propertiesFile = Join-Path $project 'local.properties'
if (Test-Path -LiteralPath $propertiesFile -PathType Leaf) {
    $line = Get-Content -LiteralPath $propertiesFile -Encoding UTF8 |
        Where-Object { $_ -match '^\s*sdk\.dir\s*=' } | Select-Object -First 1
    if ($line) {
        $value = ($line -replace '^\s*sdk\.dir\s*=\s*', '').Trim()
        $value = $value.Replace('\:', ':').Replace('\\', '\')
        if ($value) { $possibleSdk.Add($value) }
    }
}
$sdk = $null
foreach ($candidate in $possibleSdk) {
    if ($candidate -and (Test-Path -LiteralPath (Join-Path $candidate 'platforms\android-35') -PathType Container)) {
        $sdk = (Resolve-Path -LiteralPath $candidate).Path
        break
    }
}
if (-not $sdk) { throw 'Android SDK Platform 35 missing. Install Android Studio SDK Platform 35 or set ANDROID_HOME.' }
$env:ANDROID_HOME = $sdk
$env:ANDROID_SDK_ROOT = $sdk

$javaExe = $null
if ($env:JAVA_HOME) {
    $candidate = Join-Path $env:JAVA_HOME 'bin\java.exe'
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { $javaExe = $candidate }
}
if (-not $javaExe) {
    foreach ($base in @([Environment]::GetFolderPath('ProgramFiles'),[Environment]::GetFolderPath('ProgramFilesX86'))) {
        if (-not $base) { continue }
        $candidateHome = Join-Path $base 'Android\Android Studio\jbr'
        $candidate = Join-Path $candidateHome 'bin\java.exe'
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $env:JAVA_HOME = $candidateHome
            $javaExe = $candidate
            break
        }
    }
}
if (-not $javaExe) { throw 'Java not found. Install Android Studio or set JAVA_HOME to JDK 17 or later.' }
Write-Host ('Android SDK: ' + $sdk)
Write-Host ('Java: ' + $javaExe)

# AGP 8.7.3 expects Gradle 8.9. No wrapper JAR is currently committed.
$version = '8.9'
$gradleHome = Join-Path $toolsRoot ('gradle-' + $version)
$gradleBat = Join-Path $gradleHome 'bin\gradle.bat'
if (-not (Test-Path -LiteralPath $gradleBat -PathType Leaf)) {
    $zipName = 'gradle-' + $version + '-bin.zip'
    $url = 'https://services.gradle.org/distributions/' + $zipName
    $stage = Join-Path $toolsRoot ('stage-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $stage | Out-Null
    try {
        $zip = Join-Path $stage $zipName
        $sumFile = Join-Path $stage ($zipName + '.sha256')
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Write-Host ('Downloading Gradle ' + $version + ' from official distribution...')
        Invoke-WebRequest -Uri $url -UseBasicParsing -OutFile $zip
        Invoke-WebRequest -Uri ($url + '.sha256') -UseBasicParsing -OutFile $sumFile
        $expected = ((Get-Content -LiteralPath $sumFile -Raw -Encoding ASCII).Trim() -split '\s+')[0]
        if ($expected -notmatch '^[0-9a-fA-F]{64}$') { throw 'Invalid Gradle SHA-256 checksum' }
        if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash -ine $expected) {
            throw 'Gradle SHA-256 mismatch. Download rejected.'
        }
        Expand-Archive -LiteralPath $zip -DestinationPath $stage -Force
        $unpacked = Join-Path $stage ('gradle-' + $version)
        if (-not (Test-Path -LiteralPath (Join-Path $unpacked 'bin\gradle.bat') -PathType Leaf)) {
            throw 'Unexpected Gradle archive structure'
        }
        if (-not (Test-Path -LiteralPath $gradleBat)) { Move-Item -LiteralPath $unpacked -Destination $gradleHome }
    } finally {
        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    }
}
if (-not (Test-Path -LiteralPath $gradleBat -PathType Leaf)) { throw ('Gradle missing at ' + $gradleBat) }

# Manual debug build only. Does not touch server binaries, LeshyBrain or Windows service.
Write-Host 'Building Android debug APK...'
Push-Location -LiteralPath $project
try {
    & $gradleBat --no-daemon --console=plain :app:assembleDebug
    if ($LASTEXITCODE -ne 0) { throw ('Gradle assembleDebug failed: exit code ' + $LASTEXITCODE) }
} finally {
    Pop-Location
}
$sourceApk = Join-Path $project 'app\build\outputs\apk\debug\app-debug.apk'
if (-not (Test-Path -LiteralPath $sourceApk -PathType Leaf)) { throw ('APK not produced: ' + $sourceApk) }
$target = Join-Path $apkRoot 'Leshy-Android-debug.apk'
Copy-Item -LiteralPath $sourceApk -Destination $target -Force
Write-Host ('ANDROID_APK: ' + $target)
Write-Host ('ANDROID_SHA256: ' + (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash)
Write-Host 'Debug APK built; no on-device testing or release signing has been performed.'
