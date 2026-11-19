# Windows Task Scheduler: automatic Git build every 15 minutes

This is a Windows Task Scheduler job, not an AutoBuilder UI timer or Startup-folder entry.

## Install (once)

Open **Windows PowerShell as Administrator**, signed into the account that owns the repository checkout, then run:

    cd C:\Users\JASTER\Documents\GitHub\leshy-AI
    git switch main
    git pull --ff-only origin main
    .\windows\install-scheduled-build-task.ps1

The installer registers **Leshy AI - Git Build (15 min)** and starts the first check immediately. It also checks at user logon, and then runs every 15 minutes. Task principal is the signed-in Windows account with **Run with highest privileges**. No password is stored; the user must be signed in for checks to run.

The action is **wscript.exe //B //Nologo windows\scheduled-build-hidden.vbs**. WScript hides the PowerShell window. Builds use the existing build.ps1 state cache: when the commit, VERSION and published runtime have not changed, compilation is skipped. Overlapping jobs are prevented by the same named mutex used by autobuilder-core.ps1 and by Task Scheduler's IgnoreNew policy. LeshyBrain is preserved by the existing build publication guard.

## Autonomous monitoring and runtime lifecycle

The task itself is the persistent monitor, independent of the AI response loop. Every 15 minutes it fetches origin/main, fast-forwards the local checkout without rewriting local changes, and compares **Git HEAD SHA + VERSION + deployment state + bundled VERSION** against the actual Windows output. An unchanged deployment does not invoke PyInstaller or the installer; the monitor only starts the existing executable if it unexpectedly stopped. A mismatch invokes windows/build.ps1, which packages the Windows executable, runs frozen health and chat checks, assembles the installer, safely publishes into dist/Leshy and restarts the application. The existing rollback/idle-session protections remain in force. Errors and attempts are logged at .autobuild/logs/scheduled-build.log.

The Windows executable is built with a graphical subsystem (no flashing console) and carries the **same Leshy forest guardian icon** as the system tray, Inno Setup icon, Android app and browser favicon.

**System tray:** right-click the Leshy icon for **Open Leshy**, **Check for updates**, and **Exit Leshy**. Exiting requests a graceful server shutdown and creates dist/Leshy/LeshyBrain/manual-stop.flag. The monitor continues to check and build source changes but **does not automatically restart** Leshy until the owner starts Leshy.exe manually. Starting the executable directly clears this flag. Do not delete the flag during deployment.

**Signed-in user:** The Task Scheduler registration currently uses the installing administrator's interactive token to keep the tray visible in their desktop session. A logged-off or locked account is not the same as an interactive user with a desktop; this installer requires a signed-in session. Do not enable passwordless autologon or change password policy silently as part of updates. Any Windows autologon is a separate, explicitly configured security decision.


Every check performs:

    cd <this checkout>
    git switch main
    git fetch origin
    git pull --ff-only origin main
    Get-Content .\VERSION
    git rev-parse --short HEAD
    .\windows\build.ps1

An error stops the sequence; local changes are never discarded or force-reset. On successful publication, the Leshy.exe process inherits the task's elevated token and starts with no console window or browser pop-up. Manual builds retain the normal UAC behavior. When source and deployment already match, the build command is skipped altogether.

## Status, logs, removal

    Get-ScheduledTask -TaskName 'Leshy AI - Git Build (15 min)' | Get-ScheduledTaskInfo
    Get-Content .\.autobuild\logs\scheduled-build.log -Tail 80

To remove the job (also from elevated PowerShell):

    .\windows\install-scheduled-build-task.ps1 -Uninstall

The task can be inspected in taskschd.msc under **Task Scheduler Library**. Logging is rotated to scheduled-build.previous.log when the current file exceeds 10 MB.

Note: a Windows account that is not in the Administrators group cannot use the elevated scheduled task as designed. The task has not been installed remotely by updating GitHub; the one-time local installation command above is required.
