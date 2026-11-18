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

Every check performs:

    cd <this checkout>
    git switch main
    git fetch origin
    git pull --ff-only origin main
    Get-Content .\VERSION
    git rev-parse --short HEAD
    .\windows\build.ps1

An error stops the sequence; local changes are never discarded or force-reset. On successful publication, the Leshy.exe process inherits the task's elevated token and starts with no console window or browser pop-up. Manual builds retain the normal UAC behavior.

## Status, logs, removal

    Get-ScheduledTask -TaskName 'Leshy AI - Git Build (15 min)' | Get-ScheduledTaskInfo
    Get-Content .\.autobuild\logs\scheduled-build.log -Tail 80

To remove the job (also from elevated PowerShell):

    .\windows\install-scheduled-build-task.ps1 -Uninstall

The task can be inspected in taskschd.msc under **Task Scheduler Library**. Logging is rotated to scheduled-build.previous.log when the current file exceeds 10 MB.

Note: a Windows account that is not in the Administrators group cannot use the elevated scheduled task as designed. The task has not been installed remotely by updating GitHub; the one-time local installation command above is required.
