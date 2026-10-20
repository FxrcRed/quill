' Double-click to start voice input without a console window
Set fso = CreateObject("Scripting.FileSystemObject")
d = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = d
sh.Run """" & d & "\.venv\Scripts\pythonw.exe"" """ & d & "\app.pyw""", 0, False
