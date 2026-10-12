Option Explicit
Dim fs, shell, root
Set fs = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fs.GetParentFolderName(WScript.ScriptFullName)
shell.Run "wscript.exe """ & root & "\Start_Robot_UI.vbs"" /demo", 0, False
