' Yu-Mirror · 鸣潮 silent launcher (no console window)
Option Explicit
Dim fso, sh, root, pythonw
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = root & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pythonw) Then
  MsgBox "Virtual environment not found. Please run start.bat once to install dependencies.", 48, "Yu-Mirror · 鸣潮"
  WScript.Quit 1
End If
sh.CurrentDirectory = root
sh.Run """" & pythonw & """ main.py", 0, False