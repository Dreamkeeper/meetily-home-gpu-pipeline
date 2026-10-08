' Launches start_service.ps1 with no console window at all.
' "powershell -WindowStyle Hidden" still flashes a console before it hides itself;
' WScript.Shell Run with intWindowStyle=0 never creates one. Used by the
' "MeetingService-Watchdog" scheduled task (every 10 min) and the Startup-folder shortcut.
Dim sh, base
Set sh = CreateObject("WScript.Shell")
base = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
sh.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ & base & "start_service.ps1""", 0, True
