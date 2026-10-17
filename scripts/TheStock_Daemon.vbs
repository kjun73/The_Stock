' The Stock Sidecar Daemon (hidden window, auto-start on Windows boot)
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run Chr(34) & "d:\Jun_Project_D\The_Stock\.venv\Scripts\pythonw.exe" & Chr(34) & " " & Chr(34) & "d:\Jun_Project_D\The_Stock\daemon\runner.py" & Chr(34), 0, False
Set WshShell = Nothing
