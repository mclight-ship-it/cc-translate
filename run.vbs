' Silently launch CC Translate from wherever this script lives.
Set fso = CreateObject("Scripting.FileSystemObject")
Set WshShell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
target = fso.BuildPath(scriptDir, "translator.pyw")
' pythonw.exe is resolved from PATH; "start" via the shell handles the .pyw.
WshShell.Run "pythonw.exe """ & target & """", 0, False
