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

function Read-LiveState {
    if (-not (Test-Path -LiteralPath $script:LiveStatePath)) { return $null }
    try { return Get-Content -LiteralPath $script:LiveStatePath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return $null }
}

function Tail-Log {
    if (-not (Test-Path -LiteralPath $script:CoreLog)) { return '' }
    try { return (@(Get-Content -LiteralPath $script:CoreLog -Tail 100 -Encoding UTF8) -join [Environment]::NewLine) } catch { return '' }
}

function Quote-Argument {
    param([string]$Value)
    return '"' + (($Value -replace '"','').Trim()) + '"'
}

function New-Label {
    param([string]$Text, [int]$X, [int]$Y, [int]$W, [int]$H = 24, [float]$Size = 9, [bool]$Bold = $false)
    $l = New-Object System.Windows.Forms.Label
    $l.Text = $Text
    $l.Location = New-Object System.Drawing.Point($X,$Y)
    $l.Size = New-Object System.Drawing.Size($W,$H)
    $style = if ($Bold) { [System.Drawing.FontStyle]::Bold } else { [System.Drawing.FontStyle]::Regular }
    $l.Font = New-Object System.Drawing.Font('Segoe UI',$Size,$style)
    $l.ForeColor = [System.Drawing.Color]::FromArgb(225,240,231)
    return $l
}

function New-Button {
    param([string]$Text, [int]$X, [int]$Y, [int]$W, [int]$H = 38)
    $b = New-Object System.Windows.Forms.Button
    $b.Text = $Text
    $b.Location = New-Object System.Drawing.Point($X,$Y)
    $b.Size = New-Object System.Drawing.Size($W,$H)
    $b.FlatStyle = 'Flat'
    $b.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(72,132,96)
    $b.BackColor = [System.Drawing.Color]::FromArgb(20,52,37)
    $b.ForeColor = [System.Drawing.Color]::FromArgb(235,247,239)
    $b.Font = New-Object System.Drawing.Font('Segoe UI',9)
    return $b
}

function Start-CoreOperation {
    param([string]$Mode, [string]$Label)
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
    $updateNow.Enabled = -not $Busy
    $repoUpdate.Enabled = -not $Busy
    $startLeshy.Enabled = -not $Busy
    $stopLeshy.Enabled = -not $Busy
    $repoSettingsButton.Enabled = -not $Busy
    if ($Busy) { $stateLabel.Text = $T.busy; $stateLabel.ForeColor = [System.Drawing.Color]::FromArgb(240,205,130) }
    else { $stateLabel.Text = $T.ready; $stateLabel.ForeColor = [System.Drawing.Color]::FromArgb(132,221,166) }
}

function Open-RepositorySettings {
    $dialog = New-Object System.Windows.Forms.Form
    $dialog.Text = $T.repository_settings_title
    $dialog.Size = New-Object System.Drawing.Size(720,340)
    $dialog.FormBorderStyle = 'FixedDialog'
    $dialog.MaximizeBox = $false
    $dialog.MinimizeBox = $false
    $dialog.StartPosition = 'CenterParent'
    $dialog.BackColor = [System.Drawing.Color]::FromArgb(6,16,12)
    $dialog.ForeColor = [System.Drawing.Color]::FromArgb(235,247,239)
    $dialog.Font = New-Object System.Drawing.Font('Segoe UI',9)

    $pathLabel = New-Label $T.repository_path 20 20 650 22 9 $false
    $pathBox = New-Object System.Windows.Forms.TextBox
    $pathBox.Location = New-Object System.Drawing.Point(20,46)
    $pathBox.Size = New-Object System.Drawing.Size(565,25)
    $pathBox.Text = $script:RepositoryPath
    $browse = New-Button $T.browse 595 44 90 29

    $remoteLabel = New-Label $T.remote_repo 20 88 650 22 9 $false
    $remoteBox = New-Object System.Windows.Forms.TextBox
    $remoteBox.Location = New-Object System.Drawing.Point(20,114)
    $remoteBox.Size = New-Object System.Drawing.Size(450,25)
    $remoteBox.Text = $script:RemoteSpec
    $branchLabel = New-Label $T.branch 490 88 195 22 9 $false
    $branchBox = New-Object System.Windows.Forms.TextBox
    $branchBox.Location = New-Object System.Drawing.Point(490,114)
    $branchBox.Size = New-Object System.Drawing.Size(195,25)
    $branchBox.Text = $script:BranchName

    $originCaption = New-Label $T.origin_now 20 154 130 22 8.5 $false
    $originValue = New-Label '-' 150 154 535 42 8.5 $false
    $originValue.ForeColor = [System.Drawing.Color]::FromArgb(145,168,154)
    $hint = New-Label $T.remote_hint 20 202 665 34 8.5 $false
    $hint.ForeColor = [System.Drawing.Color]::FromArgb(145,168,154)

    $save = New-Button $T.save 465 252 105 34
    $cancel = New-Button $T.cancel 580 252 105 34
    $dialog.Controls.AddRange(@($pathLabel,$pathBox,$browse,$remoteLabel,$remoteBox,$branchLabel,$branchBox,$originCaption,$originValue,$hint,$save,$cancel))

    $refreshOrigin = {
        $origin = Get-OriginUrl -Path $pathBox.Text.Trim()
        $originValue.Text = if ($origin) { $origin } else { '-' }
    }
    & $refreshOrigin

    $browse.Add_Click({
        $folder = New-Object System.Windows.Forms.FolderBrowserDialog
        $folder.Description = $T.repository_path
        if (Test-Path -LiteralPath $pathBox.Text -PathType Container) { $folder.SelectedPath = $pathBox.Text }
        if ($folder.ShowDialog($dialog) -eq [System.Windows.Forms.DialogResult]::OK) {
            $pathBox.Text = $folder.SelectedPath
            & $refreshOrigin
        }
        $folder.Dispose()
    })
    $pathBox.Add_Leave({ & $refreshOrigin })
    $cancel.Add_Click({ $dialog.DialogResult = [System.Windows.Forms.DialogResult]::Cancel; $dialog.Close() })
    $save.Add_Click({
        $newPath = $pathBox.Text.Trim()
        $newRemote = $remoteBox.Text.Trim()
        $newBranch = $branchBox.Text.Trim()
        if (-not (Test-GitRepository -Path $newPath)) {
            [void][System.Windows.Forms.MessageBox]::Show($dialog,$T.invalid_repo,$T.repository_settings_title,[System.Windows.Forms.MessageBoxButtons]::OK,[System.Windows.Forms.MessageBoxIcon]::Warning)
            return
        }
        if (-not $newRemote) { $newRemote = 'origin' }
        if (-not $newBranch) { $newBranch = 'main' }
        Set-RepositoryContext -Path $newPath -Remote $newRemote -Branch $newBranch
        Save-Settings
        Refresh-Ui
        $dialog.DialogResult = [System.Windows.Forms.DialogResult]::OK
        $dialog.Close()
    })
    [void]$dialog.ShowDialog($form)
    $dialog.Dispose()
}

function Refresh-Ui {
    $sha = Get-RepoSha
    $state = Read-LiveState
    $running = Test-LeshyRunning
    $leaf = [System.IO.Path]::GetFileName($script:RepositoryPath.TrimEnd('\'))
    $short = if ($sha) { $sha.Substring(0,[Math]::Min(10,$sha.Length)) } else { '-' }
    $repoValue.Text = $short + $(if ($leaf) { '  ' + $leaf } else { '' })
    $repoToolTip.SetToolTip($repoValue, $T.repository_path + ': ' + $script:RepositoryPath + [Environment]::NewLine + $T.remote_repo + ': ' + $script:RemoteSpec + [Environment]::NewLine + $T.branch + ': ' + $script:BranchName)
    if ($state) {
        $depSha = [string]$state.sha
        $depShort = if ($depSha) { $depSha.Substring(0,[Math]::Min(10,$depSha.Length)) } else { '-' }
        $deployedValue.Text = ([string]$state.version) + '  ' + $depShort
        if ($sha -and $depSha -and $sha -ne $depSha) { $deployedValue.ForeColor = [System.Drawing.Color]::FromArgb(241,207,141) }
        else { $deployedValue.ForeColor = [System.Drawing.Color]::FromArgb(132,221,166) }
    } else {
        $deployedValue.Text = $T.not_built
        $deployedValue.ForeColor = [System.Drawing.Color]::FromArgb(241,207,141)
    }
    $appValue.Text = if ($running) { $T.running } else { $T.stopped }
    $appValue.ForeColor = if ($running) { [System.Drawing.Color]::FromArgb(132,221,166) } else { [System.Drawing.Color]::FromArgb(255,158,158) }
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

$form = New-Object System.Windows.Forms.Form
$form.Text = $T.title
$form.Size = New-Object System.Drawing.Size(830,690)
$form.MinimumSize = New-Object System.Drawing.Size(760,620)
$form.StartPosition = 'CenterScreen'
$form.BackColor = [System.Drawing.Color]::FromArgb(6,16,12)
$form.ForeColor = [System.Drawing.Color]::FromArgb(235,247,239)
$form.Font = New-Object System.Drawing.Font('Segoe UI',9)

$title = New-Label $T.title 24 18 500 36 18 $true
$subtitle = New-Label $T.subtitle 26 52 600 24 9 $false
$subtitle.ForeColor = [System.Drawing.Color]::FromArgb(145,168,154)
$stateLabel = New-Label $T.ready 650 26 130 28 10 $true
$stateLabel.TextAlign = 'MiddleRight'
$stateLabel.ForeColor = [System.Drawing.Color]::FromArgb(132,221,166)
$form.Controls.AddRange(@($title,$subtitle,$stateLabel))

$repoCaption = New-Label $T.repo_status 26 95 190 22 9 $false
$repoValue = New-Label '-' 220 95 380 22 9 $true
$repoSettingsButton = New-Button $T.repository_settings 610 90 166 31
$appCaption = New-Label $T.app_status 26 124 190 22 9 $false
$appValue = New-Label '-' 220 124 540 22 9 $true
$depCaption = New-Label $T.deployed_status 26 153 190 22 9 $false
$deployedValue = New-Label '-' 220 153 540 22 9 $true
$lastCaption = New-Label $T.last_action 26 182 190 22 9 $false
$lastActionValue = New-Label '-' 220 182 540 40 9 $false
$repoToolTip = New-Object System.Windows.Forms.ToolTip
$form.Controls.AddRange(@($repoCaption,$repoValue,$repoSettingsButton,$appCaption,$appValue,$depCaption,$deployedValue,$lastCaption,$lastActionValue))

$updateNow = New-Button $T.update_now 26 235 180 42
$repoUpdate = New-Button $T.repo_update 216 235 220 42
$startLeshy = New-Button $T.start_leshy 446 235 160 42
$stopLeshy = New-Button $T.stop_leshy 616 235 160 42
$form.Controls.AddRange(@($updateNow,$repoUpdate,$startLeshy,$stopLeshy))

$autoRepo = New-Object System.Windows.Forms.CheckBox
$autoRepo.Text = $T.auto_repo
$autoRepo.Location = New-Object System.Drawing.Point(28,300)
$autoRepo.Size = New-Object System.Drawing.Size(450,25)
$autoRepo.Checked = [bool]$S.auto_repo
$autoRepo.ForeColor = $form.ForeColor
$autoRepo.BackColor = $form.BackColor
$repoEvery = New-Label $T.repo_every 500 300 150 24 9 $false
$repoSeconds = New-Object System.Windows.Forms.NumericUpDown
$repoSeconds.Location = New-Object System.Drawing.Point(660,299)
$repoSeconds.Size = New-Object System.Drawing.Size(110,24)
$repoSeconds.Minimum = 15
$repoSeconds.Maximum = 3600
$repoSeconds.Value = [Math]::Max(15,[Math]::Min(3600,[int]$S.repo_seconds))
$form.Controls.AddRange(@($autoRepo,$repoEvery,$repoSeconds))

$scheduledBuild = New-Object System.Windows.Forms.CheckBox
$scheduledBuild.Text = $T.scheduled_build
$scheduledBuild.Location = New-Object System.Drawing.Point(28,335)
$scheduledBuild.Size = New-Object System.Drawing.Size(450,25)
$scheduledBuild.Checked = [bool]$S.scheduled_build
$scheduledBuild.ForeColor = $form.ForeColor
$scheduledBuild.BackColor = $form.BackColor
$buildEvery = New-Label $T.build_every 500 335 150 24 9 $false
$buildMinutes = New-Object System.Windows.Forms.NumericUpDown
$buildMinutes.Location = New-Object System.Drawing.Point(660,334)
$buildMinutes.Size = New-Object System.Drawing.Size(110,24)
$buildMinutes.Minimum = 1
$buildMinutes.Maximum = 1440
$buildMinutes.Value = [Math]::Max(1,[Math]::Min(1440,[int]$S.build_minutes))
$form.Controls.AddRange(@($scheduledBuild,$buildEvery,$buildMinutes))

$dataSafe = New-Label $T.data_safe 28 374 740 24 9 $true
$dataSafe.ForeColor = [System.Drawing.Color]::FromArgb(132,221,166)
$startupNote = New-Label $T.startup_note 28 400 740 44 8.5 $false
$startupNote.ForeColor = [System.Drawing.Color]::FromArgb(145,168,154)
$form.Controls.AddRange(@($dataSafe,$startupNote))

$logsCaption = New-Label $T.logs 26 447 500 24 10 $true
$logBox = New-Object System.Windows.Forms.TextBox
$logBox.Location = New-Object System.Drawing.Point(26,476)
$logBox.Size = New-Object System.Drawing.Size(750,135)
$logBox.Anchor = 'Top,Bottom,Left,Right'
$logBox.Multiline = $true
$logBox.ReadOnly = $true
$logBox.ScrollBars = 'Vertical'
$logBox.BackColor = [System.Drawing.Color]::FromArgb(4,11,8)
$logBox.ForeColor = [System.Drawing.Color]::FromArgb(198,218,205)
$logBox.Font = New-Object System.Drawing.Font('Consolas',8.5)
$form.Controls.AddRange(@($logsCaption,$logBox))

$closeHint = New-Label $T.close_hint 26 620 740 24 8.5 $false
$closeHint.Anchor = 'Bottom,Left,Right'
$closeHint.ForeColor = [System.Drawing.Color]::FromArgb(145,168,154)
$form.Controls.Add($closeHint)

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

$repoSettingsButton.Add_Click({ Open-RepositorySettings })
$updateNow.Add_Click({ Start-CoreOperation 'full-update' $T.full_update_started })
$repoUpdate.Add_Click({ Start-CoreOperation 'repo-update' $T.repo_updated })
$startLeshy.Add_Click({ Start-CoreOperation 'launch' $T.start_leshy })
$stopLeshy.Add_Click({ Start-CoreOperation 'stop' $T.stop_leshy })
$autoRepo.Add_CheckedChanged({ Save-Settings })
$repoSeconds.Add_ValueChanged({ Save-Settings })
$scheduledBuild.Add_CheckedChanged({ Save-Settings })
$buildMinutes.Add_ValueChanged({ Save-Settings })

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
Refresh-Ui
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
$repoToolTip.Dispose()
