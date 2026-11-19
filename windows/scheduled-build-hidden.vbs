Option Explicit
' Launch scheduled-build-task.ps1 without showing a console window.
Dim shell, fs, here, ps, command, code
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
here = fs.GetParentFolderName(WScript.ScriptFullName)
ps = shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\WindowsPowerShell\v1.0\powershell.exe"
command = """" & ps & """ -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ & here & "\scheduled-build-task.ps1"""
code = shell.Run(command, 0, True)
WScript.Quit code
