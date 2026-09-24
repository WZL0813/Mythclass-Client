' Launch the installer GUI without a console window.
' Keep this file ASCII-only and WITHOUT a BOM: the VBScript parser
' rejects a UTF-8 BOM as an invalid character at line 1.
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
ps = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "
cmd = ps & """" & here & "\gui.ps1"" -Source """ & here & """"
shell.Run cmd, 0, False
