‘ 让安装向导悄悄起来，不带黑框
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & here & "\gui.ps1"" -Source """ & here & """"
shell.Run cmd, 0, False
