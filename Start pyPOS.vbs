' Source checkout launcher for Windows. Open this file, not run.bat, to avoid cmd.
Option Explicit
Dim shell, fso, folder, cmd, code
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = "pyw -3 """ & folder & "\start_source.py"""
On Error Resume Next
code = shell.Run(cmd, 0, False)
If Err.Number <> 0 Then
  Err.Clear
  cmd = "pythonw """ & folder & "\start_source.py"""
  code = shell.Run(cmd, 0, False)
  If Err.Number <> 0 Then
    MsgBox "Python 3.10 or later was not found. Install Python, then open Start pyPOS.vbs again.", 16, "pyPOS"
  End If
End If
