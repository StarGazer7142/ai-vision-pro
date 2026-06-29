' start_silent.vbs - 静默启动，无黑窗口
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd /c ""cd /d D:\Project && start_all_dev.bat""", 0, False
