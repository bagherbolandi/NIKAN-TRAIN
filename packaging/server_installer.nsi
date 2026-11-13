Unicode true
!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
Name "Nikan HRM Network Server"
OutFile "${OUTPUT}"
InstallDir "$PROGRAMFILES64\NikanTrainingServer"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
!define MUI_ICON "nikan.ico"
!define MUI_UNICON "nikan.ico"
Var Hostname
Var Port
Var Mirror
Var HostInput
Var PortInput
Var MirrorInput
!insertmacro MUI_PAGE_WELCOME
Page custom NetworkPage NetworkLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Farsi"
!insertmacro MUI_LANGUAGE "English"
Function .onInit
 ${IfNot} ${RunningX64}
  Abort "64-bit Windows is required."
 ${EndIf}
 ${IfNot} ${AtLeastWin10}
  Abort "Windows 10/11 or Server 2016+ is required."
 ${EndIf}
 SetShellVarContext all
 ReadEnvStr $Hostname "COMPUTERNAME"
 StrCpy $Port "8766"
 StrCpy $Mirror ""
FunctionEnd
Function NetworkPage
 nsDialogs::Create 1018
 Pop $0
 ${NSD_CreateLabel} 0 0 100% 30u "تنظیم سرور مرکزی — کلاینت‌ها با HTTPS از مرورگر متصل می‌شوند. نام میزبان باید در شبکه قابل شناسایی باشد."
 Pop $0
 ${NSD_CreateLabel} 0 36u 100% 12u "نام میزبان / DNS"
 Pop $0
 ${NSD_CreateText} 0 50u 100% 14u "$Hostname"
 Pop $HostInput
 ${NSD_CreateLabel} 0 72u 100% 12u "پورت HTTPS (پیش‌فرض 8766)"
 Pop $0
 ${NSD_CreateText} 0 86u 100% 14u "$Port"
 Pop $PortInput
 ${NSD_CreateLabel} 0 108u 100% 20u "پوشه دوم پشتیبان (اختیاری؛ مسیر UNC موجود با دسترسی حساب سرور)"
 Pop $0
 ${NSD_CreateText} 0 132u 100% 14u "$Mirror"
 Pop $MirrorInput
 nsDialogs::Show
FunctionEnd
Function NetworkLeave
 ${NSD_GetText} $HostInput $Hostname
 ${NSD_GetText} $PortInput $Port
 ${NSD_GetText} $MirrorInput $Mirror
FunctionEnd
Section "Network Server"
 InitPluginsDir
 SetOutPath "$PLUGINSDIR"
 File /oname=preflight.ps1 "preflight_install.ps1"
 File /oname=preflight_database.py "preflight_database.py"
 nsExec::ExecToStack 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\preflight.ps1" -InstallDir "$INSTDIR" -DataDir "$APPDATA\NikanTrainingServer" -SnapshotScript "$PLUGINSDIR\preflight_database.py" -Server'
 Pop $0
 Pop $1
 ${If} $0 != 0
  MessageBox MB_ICONSTOP "$1"
  SetErrorLevel 1
  Abort
 ${EndIf}
 SetOutPath "$INSTDIR"
 File /r "${PAYLOAD}/*"
 FileOpen $0 "$INSTDIR\install.ini" w
 FileWriteUTF16LE /BOM $0 ""
 FileClose $0
 WriteINIStr "$INSTDIR\install.ini" "server" "hostname" "$Hostname"
 WriteINIStr "$INSTDIR\install.ini" "server" "port" "$Port"
 WriteINIStr "$INSTDIR\install.ini" "server" "mirror" "$Mirror"
 nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\install_server.ps1" -InstallDir "$INSTDIR"'
 Pop $0
 ${If} $0 != 0
  SetErrorLevel 1
  Abort "Server configuration failed. Check the installation log; no existing business data was removed."
 ${EndIf}
 ReadINIStr $Hostname "$INSTDIR\verified-install.ini" "server" "hostname"
 ReadINIStr $Port "$INSTDIR\verified-install.ini" "server" "port"
 WriteUninstaller "$INSTDIR\Uninstall.exe"
 CreateDirectory "$SMPROGRAMS\Nikan Training Network"
 WriteINIStr "$SMPROGRAMS\Nikan Training Network\Setup administrator.url" "InternetShortcut" "URL" "https://localhost:$Port"
 CreateShortcut "$SMPROGRAMS\Nikan Training Network\Verified recovery.lnk" "powershell.exe" '-NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\restore_server.ps1"' "$INSTDIR\nikan.ico"
 CreateShortcut "$SMPROGRAMS\Nikan Training Network\Stop server.lnk" "powershell.exe" '-NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\stop_server.ps1"' "$INSTDIR\nikan.ico"
 CreateShortcut "$SMPROGRAMS\Nikan Training Network\Start server.lnk" "powershell.exe" '-NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\start_server.ps1"' "$INSTDIR\nikan.ico"
 WriteINIStr "$DESKTOP\Nikan Training Network.url" "InternetShortcut" "URL" "https://$Hostname:$Port"
 WriteINIStr "$DESKTOP\Nikan Training Network.url" "InternetShortcut" "IconFile" "$INSTDIR\nikan.ico"
 WriteINIStr "$DESKTOP\Nikan Training Network.url" "InternetShortcut" "IconIndex" "0"
 WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanTrainingServer" "DisplayName" "Nikan HRM Network Server"
 WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanTrainingServer" "DisplayVersion" "0.13.0"
 WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanTrainingServer" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
SectionEnd
Section "Uninstall"
 nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\stop_server.ps1"'
 Pop $0
 ${If} $0 != 0
  Abort "Server must stop cleanly before uninstalling."
 ${EndIf}
 nsExec::ExecToLog 'powershell.exe -NoProfile -Command "Unregister-ScheduledTask -TaskName NikanTrainingServer -Confirm:$$false; Get-NetFirewallRule -Name NikanTrainingHTTPS -ErrorAction SilentlyContinue | Remove-NetFirewallRule"'
 Pop $0
 SetShellVarContext all
 Delete "$DESKTOP\Nikan Training Network.url"
 RMDir /r "$SMPROGRAMS\Nikan Training Network"
 DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanTrainingServer"
 RMDir /r "$INSTDIR"
 ; Keep ProgramData\NikanTrainingServer, backups, recovery key and TLS identity.
SectionEnd
