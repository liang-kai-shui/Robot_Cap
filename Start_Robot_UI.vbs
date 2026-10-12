Option Explicit
Dim fs, shell, root, python, url, health, candidates, item, options
Set fs = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fs.GetParentFolderName(WScript.ScriptFullName)
url = "http://127.0.0.1:8765"
options = " --open"
If WScript.Arguments.Count > 0 Then
  If WScript.Arguments(0) = "/demo" Then
    url = "http://127.0.0.1:8766"
    options = options & " --demo --port 8766"
  End If
End If
On Error Resume Next
Set health = CreateObject("MSXML2.ServerXMLHTTP.6.0")
health.setTimeouts 600, 600, 600, 600
health.open "GET", url & "/api/health", False
health.send
If Err.Number = 0 Then
  If health.status = 200 And InStr(health.responseText, "robot-cap-webui") > 0 Then
    shell.Run url, 1, False
    WScript.Quit
  End If
End If
Err.Clear
On Error GoTo 0
candidates = Array("D:\Espressif\python_env\idf5.1_py3.11_env\Scripts\pythonw.exe", root & "\.venv\Scripts\pythonw.exe")
python = ""
For Each item In candidates
  If fs.FileExists(item) Then
    python = item
    Exit For
  End If
Next
If python = "" Then
  MsgBox "Python was not found. See docs/WEBUI.md for setup.", 48, "Robot CaP"
  WScript.Quit 1
End If
shell.CurrentDirectory = root
shell.Run """" & python & """ """ & root & "\web_control.py" & """" & options, 0, False
