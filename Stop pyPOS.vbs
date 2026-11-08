' No console. Ask the running source pyPOS server to close gracefully.
Option Explicit
Dim fs, root, state, flag, file
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
state = root & "\.pypos-run"
flag = state & "\stop.request"
If Not fs.FolderExists(state) Then
  MsgBox "This source checkout has not been started with Start pyPOS.vbs.", 48, "pyPOS"
  WScript.Quit 0
End If
If MsgBox("Finish billing and receipts first. Stop this pyPOS register?", vbYesNo + vbExclamation, "pyPOS") = vbYes Then
  Set file = fs.CreateTextFile(flag, True)
  file.WriteLine "stop"
  file.Close
End If
