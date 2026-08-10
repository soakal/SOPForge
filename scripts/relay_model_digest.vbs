' Runs relay_model_digest.cmd with no visible console window. The scheduled
' task fires on a repeating window (up to 13x/morning) -- a bare .cmd
' action under an Interactive-logon task would flash a console each time.
' Exit code is propagated so Task Scheduler's LastTaskResult means something.
Dim shell, dir, cmd, exitCode
Set shell = CreateObject("WScript.Shell")

dir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
cmd = dir & "\relay_model_digest.cmd"

shell.CurrentDirectory = dir
exitCode = shell.Run("""" & cmd & """", 0, True)
WScript.Quit(exitCode)
