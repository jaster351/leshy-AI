param(
    [switch]$Minimized
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$HostRepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$HostRuntimeRoot = Join-Path $HostRepoRoot '.autobuild'
$CoreScript = Join-Path $PSScriptRoot 'autobuilder-core.ps1'
$TextPath = Join-Path $PSScriptRoot 'autobuilder-ui.ru.json'
$SettingsPath = Join-Path $HostRuntimeRoot 'ui-settings.json'
$StartupRoot = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
$StartupCmd = Join-Path $StartupRoot 'Leshy-AutoBuilder.cmd'
$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

New-Item -ItemType Directory -Force -Path $HostRuntimeRoot | Out-Null
$T = Get-Content -LiteralPath $TextPath -Raw -Encoding UTF8 | ConvertFrom-Json

$ColorForm = [System.Drawing.Color]::FromArgb(239,242,246)
$ColorSidebar = [System.Drawing.Color]::FromArgb(52,60,71)
$ColorSidebarActive = [System.Drawing.Color]::FromArgb(66,76,89)
$ColorSidebarText = [System.Drawing.Color]::FromArgb(207,214,224)
$ColorText = [System.Drawing.Color]::FromArgb(45,55,72)
$ColorMuted = [System.Drawing.Color]::FromArgb(123,132,148)
$ColorAccent = [System.Drawing.Color]::FromArgb(63,126,188)
$ColorBorder = [System.Drawing.Color]::FromArgb(216,221,230)
$ColorCard = [System.Drawing.Color]::White
$ColorGood = [System.Drawing.Color]::FromArgb(56,122,86)
$ColorBad = [System.Drawing.Color]::FromArgb(190,73,73)
$ColorWarn = [System.Drawing.Color]::FromArgb(170,111,35)
$ColorLog = [System.Drawing.Color]::FromArgb(31,39,50)
$ColorLogText = [System.Drawing.Color]::FromArgb(218,225,234)

function Read-Settings {
    $defaults = [ordered]@{
        repository_path = $HostRepoRoot
        remote = 'origin'
        branch = 'main'
        auto_repo = $true
        repo_seconds = 60
        scheduled_build = $false
        build_minutes = 60
        last_full_update_utc = ''
        last_full_attempt_utc = ''
    }
    if (-not (Test-Path -LiteralPath $SettingsPath)) { return [pscustomobject]$defaults }
    try {
        $value = Get-Content -LiteralPath $SettingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($key in @('repository_path','remote','branch','auto_repo','repo_seconds','scheduled_build','build_minutes','last_full_update_utc','last_full_attempt_utc')) {
            if ($null -eq $value.$key) { $value | Add-Member -NotePropertyName $key -NotePropertyValue $defaults[$key] }
        }
        return $value
    } catch {
        return [pscustomobject]$defaults
    }
}

function Set-RepositoryContext {
    param([string]$Path, [string]$Remote, [string]$Branch)
    if ([string]::IsNullOrWhiteSpace($Path)) { $Path = $HostRepoRoot }
    try { $full = [System.IO.Path]::GetFullPath($Path) } catch { $full = $Path }
    $script:RepositoryPath = $full.TrimEnd('\')
    $script:RemoteSpec = if ([string]::IsNullOrWhiteSpace($Remote)) { 'origin' } else { $Remote.Trim() }
    $script:BranchName = if ([string]::IsNullOrWhiteSpace($Branch)) { 'main' } else { $Branch.Trim() }
    $script:SelectedRuntimeRoot = Join-Path $script:RepositoryPath '.autobuild'
    $script:SelectedLogsRoot = Join-Path $script:SelectedRuntimeRoot 'logs'
    $script:LiveStatePath = Join-Path $script:SelectedRuntimeRoot 'live-state.json'
    $script:CoreLog = Join-Path $script:SelectedLogsRoot 'autobuilder-gui.log'
    $script:LiveRoot = Join-Path $script:RepositoryPath 'dist\Leshy'
    $script:LiveExe = Join-Path $script:LiveRoot 'Leshy.exe'
    if (Test-Path -LiteralPath $script:RepositoryPath -PathType Container) {
        New-Item -ItemType Directory -Force -Path $script:SelectedLogsRoot | Out-Null
    }
}

function Save-Settings {
    $payload = [ordered]@{
        repository_path = [string]$script:RepositoryPath
        remote = [string]$script:RemoteSpec
        branch = [string]$script:BranchName
        auto_repo = [bool]$autoRepo.Checked
        repo_seconds = [int]$repoSeconds.Value
        scheduled_build = [bool]$scheduledBuild.Checked
        build_minutes = [int]$buildMinutes.Value
        last_full_update_utc = [string]$script:LastFullUpdateUtc
        last_full_attempt_utc = [string]$script:LastFullAttemptUtc
    }
    $payload | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $SettingsPath -Encoding UTF8
}

function Register-UserStartup {
    New-Item -ItemType Directory -Force -Path $StartupRoot | Out-Null
    $cmd = '@echo off' + [Environment]::NewLine + 'start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $PSCommandPath + '" -Minimized' + [Environment]::NewLine
    Set-Content -LiteralPath $StartupCmd -Value $cmd -Encoding ASCII
}

function Stop-LegacyWatcher {
    try {
        foreach ($proc in @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue)) {
            if ([int]$proc.ProcessId -eq $PID) { continue }
            $cmd = [string]$proc.CommandLine
            if ($cmd -and $cmd.IndexOf('autobuilder.ps1',[System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and $cmd.IndexOf('-Mode watch',[System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                Stop-Process -Id ([int]$proc.ProcessId) -Force -ErrorAction SilentlyContinue
            }
        }
    } catch { }
}

function Get-UiGit {
    $cmd = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($candidate in @(
        'C:\Program Files\Git\cmd\git.exe',
        'C:\Program Files\Git\bin\git.exe',
        (Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd\git.exe')
    )) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    return ''
}

function Test-GitRepository {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return $false }
    try {
        $git = Get-UiGit
        if (-not $git) { return $false }
        $raw = @(& $git -C $Path rev-parse --is-inside-work-tree 2>$null)
        return ($LASTEXITCODE -eq 0 -and $raw.Count -gt 0 -and ([string]$raw[0]).Trim().ToLowerInvariant() -eq 'true')
    } catch { return $false }
}

function Get-OriginUrl {
    param([string]$Path)
    try {
        $git = Get-UiGit
        if (-not $git -or -not (Test-Path -LiteralPath $Path -PathType Container)) { return '' }
        $raw = @(& $git -C $Path remote get-url origin 2>$null)
        if ($LASTEXITCODE -eq 0 -and $raw.Count -gt 0) { return ([string]$raw[0]).Trim() }
    } catch { }
    return ''
}

function Get-RepoSha {
    try {
        $git = Get-UiGit
        if (-not $git -or -not (Test-Path -LiteralPath $script:RepositoryPath -PathType Container)) { return '' }
        $raw = @(& $git -C $script:RepositoryPath rev-parse --verify HEAD 2>$null)
        if ($LASTEXITCODE -eq 0 -and $raw.Count -gt 0) { return ([string]$raw[0]).Trim() }
    } catch { }
    return ''
}

function Normalize-PathText {
    param([string]$Value)
    if (-not $Value) { return '' }
    try { return [System.IO.Path]::GetFullPath($Value).TrimEnd('\').ToLowerInvariant() } catch { return '' }
}

function Test-LeshyRunning {
    $target = Normalize-PathText $script:LiveExe
    if (-not $target) { return $false }
    try {
        foreach ($proc in @(Get-CimInstance Win32_Process -Filter "Name='Leshy.exe'" -ErrorAction SilentlyContinue)) {
            if ((Normalize-PathText ([string]$proc.ExecutablePath)) -eq $target) { return $true }
        }
    } catch { }
    return $false
}

function Read-LiveState {
    if (-not (Test-Path -LiteralPath $script:LiveStatePath)) { return $null }
    try { return Get-Content -LiteralPath $script:LiveStatePath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return $null }
}

function Tail-Log {
    if (-not (Test-Path -LiteralPath $script:CoreLog)) { return '' }
    try { return (@(Get-Content -LiteralPath $script:CoreLog -Tail 140 -Encoding UTF8) -join [Environment]::NewLine) } catch { return '' }
}

function Quote-Argument {
    param([string]$Value)
    return '"' + (($Value -replace '"','').Trim()) + '"'
}

function New-Label {
    param([string]$Text,[int]$X,[int]$Y,[int]$W,[int]$H=22,[float]$Size=9,[bool]$Bold=$false)
    $c = New-Object System.Windows.Forms.Label
    $c.Text = $Text
    $c.Location = New-Object System.Drawing.Point($X,$Y)
    $c.Size = New-Object System.Drawing.Size($W,$H)
    $style = if ($Bold) { [System.Drawing.FontStyle]::Bold } else { [System.Drawing.FontStyle]::Regular }
    $c.Font = New-Object System.Drawing.Font('Segoe UI',$Size,$style)
    $c.ForeColor = $ColorText
    return $c
}

function New-Button {
    param([string]$Text,[int]$X,[int]$Y,[int]$W,[int]$H=34,[bool]$Primary=$false)
    $c = New-Object System.Windows.Forms.Button
    $c.Text = $Text
    $c.Location = New-Object System.Drawing.Point($X,$Y)
    $c.Size = New-Object System.Drawing.Size($W,$H)
    $c.FlatStyle = 'Flat'
    $c.FlatAppearance.BorderSize = 1
    $c.Font = New-Object System.Drawing.Font('Segoe UI',9)
    if ($Primary) {
        $c.BackColor = $ColorAccent
        $c.ForeColor = [System.Drawing.Color]::White
        $c.FlatAppearance.BorderColor = $ColorAccent
    } else {
        $c.BackColor = $ColorCard
        $c.ForeColor = $ColorText
        $c.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(190,199,211)
    }
    return $c
}

function New-NavButton {
    param([string]$Text,[string]$Page,[int]$Y)
    $b = New-Object System.Windows.Forms.Button
    $b.Text = $Text
    $b.Tag = $Page
    $b.Location = New-Object System.Drawing.Point(0,$Y)
    $b.Size = New-Object System.Drawing.Size(180,40)
    $b.FlatStyle = 'Flat'
    $b.FlatAppearance.BorderSize = 0
    $b.BackColor = $ColorSidebar
    $b.ForeColor = $ColorSidebarText
    $b.Font = New-Object System.Drawing.Font('Segoe UI',9)
    $b.TextAlign = 'MiddleLeft'
    $b.Padding = New-Object System.Windows.Forms.Padding(18,0,0,0)
    return $b
}

function New-Card {
    param([int]$X,[int]$Y,[int]$W,[int]$H)
    $p = New-Object System.Windows.Forms.Panel
    $p.Location = New-Object System.Drawing.Point($X,$Y)
    $p.Size = New-Object System.Drawing.Size($W,$H)
    $p.BackColor = $ColorCard
    $p.BorderStyle = 'FixedSingle'
    return $p
}

function Start-CoreOperation {
    param([string]$Mode,[string]$Label)
    if ($script:OperationProcess -and -not $script:OperationProcess.HasExited) { return }
    $script:OperationMode = $Mode
    $script:OperationLabel = $Label
    if ($Mode -in @('full-update','startup')) {
        $script:LastFullAttemptUtc = [DateTime]::UtcNow.ToString('o')
        Save-Settings
    }
    $args = '-NoProfile -ExecutionPolicy Bypass -File ' + (Quote-Argument $CoreScript) +
        ' -Mode ' + $Mode +
        ' -RepositoryPath ' + (Quote-Argument $script:RepositoryPath) +
        ' -Remote ' + (Quote-Argument $script:RemoteSpec) +
        ' -Branch ' + (Quote-Argument $script:BranchName)
    try {
        $script:OperationProcess = Start-Process -FilePath $PowerShellExe -ArgumentList $args -WorkingDirectory $HostRepoRoot -WindowStyle Hidden -PassThru
        $lastActionValue.Text = $Label
        Set-Busy $true
    } catch {
        $lastActionValue.Text = $T.operation_failed + ': ' + $_.Exception.Message
        Set-Busy $false
    }
}

function Set-Busy {
    param([bool]$Busy)
    foreach ($c in @($updateNow,$repoUpdate,$startLeshy,$stopLeshy,$repoSave,$browseButton)) {
        if ($c) { $c.Enabled = -not $Busy }
    }
    $stateValue.Text = if ($Busy) { $T.busy } else { $T.ready }
    $stateValue.ForeColor = if ($Busy) { $ColorWarn } else { $ColorGood }
}

function Show-Page {
    param([string]$Name)
    foreach ($key in $script:Pages.Keys) { $script:Pages[$key].Visible = ($key -eq $Name) }
    foreach ($b in $script:NavButtons) {
        $b.BackColor = if ([string]$b.Tag -eq $Name) { $ColorSidebarActive } else { $ColorSidebar }
        $b.ForeColor = if ([string]$b.Tag -eq $Name) { [System.Drawing.Color]::White } else { $ColorSidebarText }
    }
    switch ($Name) {
        'overview' { $headerTitle.Text = $T.overview }
        'repository' { $headerTitle.Text = $T.repository }
        'build' { $headerTitle.Text = $T.build }
        'logs' { $headerTitle.Text = $T.logs }
    }
}

function Sync-RepositoryFields {
    $repoPathBox.Text = $script:RepositoryPath
    $remoteBox.Text = $script:RemoteSpec
    $branchBox.Text = $script:BranchName
    $originValue.Text = Get-OriginUrl -Path $script:RepositoryPath
    if (-not $originValue.Text) { $originValue.Text = '-' }
}

function Apply-RepositorySettings {
    $path = $repoPathBox.Text.Trim()
    $remote = $remoteBox.Text.Trim()
    $branch = $branchBox.Text.Trim()
    if (-not (Test-GitRepository -Path $path)) {
        [void][System.Windows.Forms.MessageBox]::Show($form,$T.invalid_repo,$T.repository_settings_title,[System.Windows.Forms.MessageBoxButtons]::OK,[System.Windows.Forms.MessageBoxIcon]::Warning)
        return
    }
    if (-not $remote) { $remote = 'origin' }
    if (-not $branch) { $branch = 'main' }
    Set-RepositoryContext -Path $path -Remote $remote -Branch $branch
    Save-Settings
    Sync-RepositoryFields
    Refresh-Ui
}

function Refresh-Ui {
    $sha = Get-RepoSha
    $state = Read-LiveState
    $running = Test-LeshyRunning
    $leaf = [System.IO.Path]::GetFileName($script:RepositoryPath.TrimEnd('\'))
    $short = if ($sha) { $sha.Substring(0,[Math]::Min(10,$sha.Length)) } else { '-' }
    $repoValue.Text = $short + $(if ($leaf) { '  ' + $leaf } else { '' })
    $repoPathSummary.Text = $script:RepositoryPath
    $remoteSummary.Text = $script:RemoteSpec + ' / ' + $script:BranchName

    if ($state) {
        $depSha = [string]$state.sha
        $depShort = if ($depSha) { $depSha.Substring(0,[Math]::Min(10,$depSha.Length)) } else { '-' }
        $deployedValue.Text = ([string]$state.version) + '  ' + $depShort
        $deployedValue.ForeColor = if ($sha -and $depSha -and $sha -ne $depSha) { $ColorWarn } else { $ColorGood }
    } else {
        $deployedValue.Text = $T.not_built
        $deployedValue.ForeColor = $ColorWarn
    }

    $appValue.Text = if ($running) { $T.running } else { $T.stopped }
    $appValue.ForeColor = if ($running) { $ColorGood } else { $ColorBad }
    $logBox.Text = Tail-Log
    $logBox.SelectionStart = $logBox.TextLength
    $logBox.ScrollToCaret()
}

$S = Read-Settings
Set-RepositoryContext -Path ([string]$S.repository_path) -Remote ([string]$S.remote) -Branch ([string]$S.branch)
$script:OperationProcess = $null
$script:OperationMode = ''
$script:OperationLabel = ''
$script:LastRepoCheck = [DateTime]::MinValue
$script:LastFullUpdateUtc = [string]$S.last_full_update_utc
$script:LastFullAttemptUtc = [string]$S.last_full_attempt_utc
$script:ReallyExit = $false
$script:StartupTriggered = $false
$script:Pages = @{}
$script:NavButtons = @()

$form = New-Object System.Windows.Forms.Form
$form.Text = $T.title
$form.Size = New-Object System.Drawing.Size(1050,720)
$form.MinimumSize = New-Object System.Drawing.Size(900,620)
$form.StartPosition = 'CenterScreen'
$form.BackColor = $ColorForm
$form.ForeColor = $ColorText
$form.Font = New-Object System.Drawing.Font('Segoe UI',9)

$sidebar = New-Object System.Windows.Forms.Panel
$sidebar.Dock = 'Left'
$sidebar.Width = 180
$sidebar.BackColor = $ColorSidebar
$form.Controls.Add($sidebar)

$brand = New-Label $T.title 18 18 150 30 15 $true
$brand.ForeColor = [System.Drawing.Color]::White
$brand.BackColor = $ColorSidebar
$sidebar.Controls.Add($brand)

$navOverview = New-NavButton $T.overview 'overview' 72
$navRepository = New-NavButton $T.repository 'repository' 114
$navBuild = New-NavButton $T.build 'build' 156
$navLogs = New-NavButton $T.logs 'logs' 198
$script:NavButtons = @($navOverview,$navRepository,$navBuild,$navLogs)
$sidebar.Controls.AddRange($script:NavButtons)

$dataNote = New-Label $T.data_safe 14 590 150 70 8 $false
$dataNote.ForeColor = [System.Drawing.Color]::FromArgb(164,174,188)
$dataNote.BackColor = $ColorSidebar
$dataNote.Anchor = 'Bottom,Left'
$sidebar.Controls.Add($dataNote)

$main = New-Object System.Windows.Forms.Panel
$main.Dock = 'Fill'
$main.BackColor = $ColorForm
$form.Controls.Add($main)
$main.BringToFront()

$header = New-Object System.Windows.Forms.Panel
$header.Dock = 'Top'
$header.Height = 60
$header.BackColor = $ColorCard
$header.BorderStyle = 'FixedSingle'
$main.Controls.Add($header)

$headerTitle = New-Label $T.overview 22 16 420 28 14 $true
$header.Controls.Add($headerTitle)
$stateCaption = New-Label $T.last_action 610 10 120 18 8 $false
$stateCaption.ForeColor = $ColorMuted
$stateCaption.Anchor = 'Top,Right'
$stateValue = New-Label $T.ready 735 10 105 20 9 $true
$stateValue.ForeColor = $ColorGood
$stateValue.TextAlign = 'MiddleRight'
$stateValue.Anchor = 'Top,Right'
$header.Controls.AddRange(@($stateCaption,$stateValue))

$content = New-Object System.Windows.Forms.Panel
$content.Dock = 'Fill'
$content.BackColor = $ColorForm
$main.Controls.Add($content)
$content.BringToFront()
$header.BringToFront()

# Overview page
$pageOverview = New-Object System.Windows.Forms.Panel
$pageOverview.Dock = 'Fill'
$pageOverview.BackColor = $ColorForm
$script:Pages['overview'] = $pageOverview
$content.Controls.Add($pageOverview)

$cardRepo = New-Card 20 18 245 105
$cardApp = New-Card 278 18 245 105
$cardDeploy = New-Card 536 18 285 105
$pageOverview.Controls.AddRange(@($cardRepo,$cardApp,$cardDeploy))
$cardRepo.Controls.Add((New-Label $T.repo_status 14 12 210 20 9 $false))
$repoValue = New-Label '-' 14 42 215 28 12 $true
$cardRepo.Controls.Add($repoValue)
$cardApp.Controls.Add((New-Label $T.app_status 14 12 210 20 9 $false))
$appValue = New-Label '-' 14 42 215 28 12 $true
$cardApp.Controls.Add($appValue)
$cardDeploy.Controls.Add((New-Label $T.deployed_status 14 12 250 20 9 $false))
$deployedValue = New-Label '-' 14 42 255 28 12 $true
$cardDeploy.Controls.Add($deployedValue)

$actionCard = New-Card 20 138 801 150
$pageOverview.Controls.Add($actionCard)
$actionCard.Controls.Add((New-Label $T.last_action 16 14 180 20 9 $false))
$lastActionValue = New-Label '-' 16 39 760 38 10 $true
$actionCard.Controls.Add($lastActionValue)
$updateNow = New-Button $T.update_now 16 94 170 36 $true
$repoUpdate = New-Button $T.repo_update 196 94 205 36 $false
$startLeshy = New-Button $T.start_leshy 411 94 170 36 $false
$stopLeshy = New-Button $T.stop_leshy 591 94 170 36 $false
$actionCard.Controls.AddRange(@($updateNow,$repoUpdate,$startLeshy,$stopLeshy))

$summaryCard = New-Card 20 302 801 170
$pageOverview.Controls.Add($summaryCard)
$summaryCard.Controls.Add((New-Label $T.repository_path 16 14 220 20 9 $false))
$repoPathSummary = New-Label '-' 16 38 760 42 9 $true
$repoPathSummary.ForeColor = $ColorText
$summaryCard.Controls.Add($repoPathSummary)
$summaryCard.Controls.Add((New-Label $T.remote_repo 16 88 220 20 9 $false))
$remoteSummary = New-Label '-' 16 112 760 30 9 $true
$summaryCard.Controls.Add($remoteSummary)

# Repository page
$pageRepository = New-Object System.Windows.Forms.Panel
$pageRepository.Dock = 'Fill'
$pageRepository.BackColor = $ColorForm
$pageRepository.Visible = $false
$script:Pages['repository'] = $pageRepository
$content.Controls.Add($pageRepository)

$repoCard = New-Card 20 18 801 345
$pageRepository.Controls.Add($repoCard)
$repoCard.Controls.Add((New-Label $T.repository_path 18 18 650 22 9 $true))
$repoPathBox = New-Object System.Windows.Forms.TextBox
$repoPathBox.Location = New-Object System.Drawing.Point(18,46)
$repoPathBox.Size = New-Object System.Drawing.Size(650,26)
$repoPathBox.BorderStyle = 'FixedSingle'
$browseButton = New-Button $T.browse 680 44 100 30 $false
$repoCard.Controls.AddRange(@($repoPathBox,$browseButton))
$repoCard.Controls.Add((New-Label $T.remote_repo 18 94 450 22 9 $true))
$remoteBox = New-Object System.Windows.Forms.TextBox
$remoteBox.Location = New-Object System.Drawing.Point(18,122)
$remoteBox.Size = New-Object System.Drawing.Size(520,26)
$remoteBox.BorderStyle = 'FixedSingle'
$repoCard.Controls.Add($remoteBox)
$repoCard.Controls.Add((New-Label $T.branch 560 94 220 22 9 $true))
$branchBox = New-Object System.Windows.Forms.TextBox
$branchBox.Location = New-Object System.Drawing.Point(560,122)
$branchBox.Size = New-Object System.Drawing.Size(220,26)
$branchBox.BorderStyle = 'FixedSingle'
$repoCard.Controls.Add($branchBox)
$repoCard.Controls.Add((New-Label $T.origin_now 18 168 160 22 9 $false))
$originValue = New-Label '-' 180 168 600 44 8.5 $false
$originValue.ForeColor = $ColorMuted
$repoCard.Controls.Add($originValue)
$hintRepo = New-Label $T.remote_hint 18 218 750 38 8.5 $false
$hintRepo.ForeColor = $ColorMuted
$repoCard.Controls.Add($hintRepo)
$repoSave = New-Button $T.save 610 278 170 38 $true
$repoCard.Controls.Add($repoSave)

# Build/settings page
$pageBuild = New-Object System.Windows.Forms.Panel
$pageBuild.Dock = 'Fill'
$pageBuild.BackColor = $ColorForm
$pageBuild.Visible = $false
$script:Pages['build'] = $pageBuild
$content.Controls.Add($pageBuild)

$buildCard = New-Card 20 18 801 250
$pageBuild.Controls.Add($buildCard)
$autoRepo = New-Object System.Windows.Forms.CheckBox
$autoRepo.Text = $T.auto_repo
$autoRepo.Location = New-Object System.Drawing.Point(18,22)
$autoRepo.Size = New-Object System.Drawing.Size(530,26)
$autoRepo.BackColor = $ColorCard
$autoRepo.ForeColor = $ColorText
$autoRepo.Checked = [bool]$S.auto_repo
$buildCard.Controls.Add($autoRepo)
$buildCard.Controls.Add((New-Label $T.repo_every 565 23 145 22 9 $false))
$repoSeconds = New-Object System.Windows.Forms.NumericUpDown
$repoSeconds.Location = New-Object System.Drawing.Point(710,21)
$repoSeconds.Size = New-Object System.Drawing.Size(70,25)
$repoSeconds.Minimum = 15
$repoSeconds.Maximum = 3600
$repoSeconds.Value = [Math]::Max(15,[Math]::Min(3600,[int]$S.repo_seconds))
$buildCard.Controls.Add($repoSeconds)

$scheduledBuild = New-Object System.Windows.Forms.CheckBox
$scheduledBuild.Text = $T.scheduled_build
$scheduledBuild.Location = New-Object System.Drawing.Point(18,68)
$scheduledBuild.Size = New-Object System.Drawing.Size(530,26)
$scheduledBuild.BackColor = $ColorCard
$scheduledBuild.ForeColor = $ColorText
$scheduledBuild.Checked = [bool]$S.scheduled_build
$buildCard.Controls.Add($scheduledBuild)
$buildCard.Controls.Add((New-Label $T.build_every 565 69 145 22 9 $false))
$buildMinutes = New-Object System.Windows.Forms.NumericUpDown
$buildMinutes.Location = New-Object System.Drawing.Point(710,67)
$buildMinutes.Size = New-Object System.Drawing.Size(70,25)
$buildMinutes.Minimum = 1
$buildMinutes.Maximum = 1440
$buildMinutes.Value = [Math]::Max(1,[Math]::Min(1440,[int]$S.build_minutes))
$buildCard.Controls.Add($buildMinutes)

$buildNote = New-Label $T.startup_note 18 120 750 52 8.5 $false
$buildNote.ForeColor = $ColorMuted
$buildCard.Controls.Add($buildNote)
$buildSafe = New-Label $T.data_safe 18 182 750 28 9 $true
$buildSafe.ForeColor = $ColorGood
$buildCard.Controls.Add($buildSafe)

# Logs page
$pageLogs = New-Object System.Windows.Forms.Panel
$pageLogs.Dock = 'Fill'
$pageLogs.BackColor = $ColorForm
$pageLogs.Visible = $false
$script:Pages['logs'] = $pageLogs
$content.Controls.Add($pageLogs)
$logCard = New-Card 20 18 801 535
$logCard.Anchor = 'Top,Bottom,Left,Right'
$pageLogs.Controls.Add($logCard)
$logTitle = New-Label $T.logs 16 14 500 24 10 $true
$logCard.Controls.Add($logTitle)
$logBox = New-Object System.Windows.Forms.TextBox
$logBox.Location = New-Object System.Drawing.Point(16,46)
$logBox.Size = New-Object System.Drawing.Size(765,468)
$logBox.Anchor = 'Top,Bottom,Left,Right'
$logBox.Multiline = $true
$logBox.ReadOnly = $true
$logBox.ScrollBars = 'Vertical'
$logBox.BackColor = $ColorLog
$logBox.ForeColor = $ColorLogText
$logBox.BorderStyle = 'FixedSingle'
$logBox.Font = New-Object System.Drawing.Font('Consolas',8.5)
$logCard.Controls.Add($logBox)

foreach ($b in $script:NavButtons) {
    $b.Add_Click({ param($sender,$e) Show-Page ([string]$sender.Tag) })
}

$browseButton.Add_Click({
    $folder = New-Object System.Windows.Forms.FolderBrowserDialog
    $folder.Description = $T.repository_path
    if (Test-Path -LiteralPath $repoPathBox.Text -PathType Container) { $folder.SelectedPath = $repoPathBox.Text }
    if ($folder.ShowDialog($form) -eq [System.Windows.Forms.DialogResult]::OK) {
        $repoPathBox.Text = $folder.SelectedPath
        $origin = Get-OriginUrl -Path $folder.SelectedPath
        $originValue.Text = if ($origin) { $origin } else { '-' }
    }
    $folder.Dispose()
})
$repoSave.Add_Click({ Apply-RepositorySettings })
$updateNow.Add_Click({ Start-CoreOperation 'full-update' $T.full_update_started })
$repoUpdate.Add_Click({ Start-CoreOperation 'repo-update' $T.repo_updated })
$startLeshy.Add_Click({ Start-CoreOperation 'launch' $T.start_leshy })
$stopLeshy.Add_Click({ Start-CoreOperation 'stop' $T.stop_leshy })
$autoRepo.Add_CheckedChanged({ Save-Settings })
$repoSeconds.Add_ValueChanged({ Save-Settings })
$scheduledBuild.Add_CheckedChanged({ Save-Settings })
$buildMinutes.Add_ValueChanged({ Save-Settings })

$tray = New-Object System.Windows.Forms.NotifyIcon
$tray.Icon = [System.Drawing.SystemIcons]::Application
$tray.Text = $T.title
$tray.Visible = $true
$menu = New-Object System.Windows.Forms.ContextMenuStrip
$miOpen = $menu.Items.Add($T.tray_open)
$miUpdate = $menu.Items.Add($T.tray_update)
$miExit = $menu.Items.Add($T.tray_exit)
$tray.ContextMenuStrip = $menu
$showWindow = {
    $form.Show()
    $form.WindowState = 'Normal'
    $form.Activate()
}
$miOpen.Add_Click($showWindow)
$tray.Add_DoubleClick($showWindow)
$miUpdate.Add_Click({ Start-CoreOperation 'full-update' $T.full_update_started })
$miExit.Add_Click({ $script:ReallyExit = $true; $tray.Visible = $false; Save-Settings; $form.Close() })

$form.Add_FormClosing({
    param($sender,$e)
    if (-not $script:ReallyExit) {
        $e.Cancel = $true
        $form.Hide()
    }
})

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 2000
$timer.Add_Tick({
    if ($script:OperationProcess -and $script:OperationProcess.HasExited) {
        $exitCode = $script:OperationProcess.ExitCode
        if ($exitCode -eq 0) {
            $lastActionValue.Text = $T.operation_ok + ': ' + $script:OperationLabel
            if ($script:OperationMode -in @('full-update','startup')) {
                $script:LastFullUpdateUtc = [DateTime]::UtcNow.ToString('o')
                $script:LastFullAttemptUtc = $script:LastFullUpdateUtc
                Save-Settings
            }
        } else {
            $lastActionValue.Text = $T.operation_failed + ': ' + $script:OperationLabel
            Save-Settings
        }
        $script:OperationProcess.Dispose()
        $script:OperationProcess = $null
        Set-Busy $false
    }

    Refresh-Ui
    if ($script:OperationProcess -and -not $script:OperationProcess.HasExited) { return }

    $now = Get-Date
    if ($scheduledBuild.Checked) {
        $due = $false
        if (-not $script:LastFullAttemptUtc) { $due = $true }
        else {
            try {
                $lastAttempt = [DateTime]::Parse($script:LastFullAttemptUtc).ToLocalTime()
                $due = (($now - $lastAttempt).TotalMinutes -ge [double]$buildMinutes.Value)
            } catch { $due = $true }
        }
        if ($due) {
            Start-CoreOperation 'full-update' $T.full_update_started
            return
        }
    }

    if ($autoRepo.Checked -and (($now - $script:LastRepoCheck).TotalSeconds -ge [double]$repoSeconds.Value)) {
        $script:LastRepoCheck = $now
        Start-CoreOperation 'repo-update' $T.repo_updated
    }
})

Register-UserStartup
Stop-LegacyWatcher
Sync-RepositoryFields
Refresh-Ui
Show-Page 'overview'
$timer.Start()

$form.Add_Shown({
    if (-not $script:StartupTriggered) {
        $script:StartupTriggered = $true
        Start-CoreOperation 'startup' $T.startup_started
    }
    if ($Minimized) { $form.Hide() }
})

[void][System.Windows.Forms.Application]::Run($form)
$timer.Stop()
$tray.Visible = $false
$tray.Dispose()
