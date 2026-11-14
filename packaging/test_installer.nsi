Unicode true
!include "MUI2.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
!ifndef PAYLOAD
 !error "Pass /DPAYLOAD=path to staged application"
!endif
!ifndef OUTPUT
 !define OUTPUT "../distribution/NikanHRM-Test-Setup.exe"
!endif
Var AppIcon
Name "Nikan HRM Test"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\NikanHRM-Test"
RequestExecutionLevel user
SetCompressor /SOLID lzma
!define MUI_ICON "nikan.ico"
!define MUI_UNICON "nikan.ico"
!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\runtime\pythonw.exe"
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchApplication
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Farsi"
!insertmacro MUI_LANGUAGE "English"
Function LaunchApplication
 Exec '"$INSTDIR\runtime\pythonw.exe" -I "$INSTDIR\desktop_test.py"'
FunctionEnd
Function .onInit
 StrCpy $INSTDIR "$LOCALAPPDATA\Programs\NikanHRM-Test"
 ${IfNot} ${RunningX64}
  MessageBox MB_ICONSTOP "This installer requires 64-bit Windows 10 or Windows 11."
  Abort
 ${EndIf}
 ${IfNot} ${AtLeastWin10}
  MessageBox MB_ICONSTOP "Windows 10 or Windows 11 is required."
  Abort
 ${EndIf}
 System::Call 'kernel32::OpenMutexW(i 0x100000, i 0, w "Local\NikanHRM-TestDesktop") p .r0'
 ${If} $0 != 0
  System::Call 'kernel32::CloseHandle(p r0)'
  MessageBox MB_ICONSTOP "Close Nikan HRM Test before installing or upgrading."
  Abort
 ${EndIf}
 SetShellVarContext current
FunctionEnd
Section "Nikan HRM Test" SEC_MAIN
 InitPluginsDir
 SetOutPath "$PLUGINSDIR"
 File /oname=preflight.ps1 "preflight_install.ps1"
 File /oname=preflight_database.py "preflight_database.py"
 nsExec::ExecToStack 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\preflight.ps1" -InstallDir "$INSTDIR" -DataDir "$LOCALAPPDATA\NikanHRM-Test" -SnapshotScript "$PLUGINSDIR\preflight_database.py"'
 Pop $0
 Pop $1
 ${If} $0 != 0
  MessageBox MB_ICONSTOP "$1"
  SetErrorLevel 1
  Abort
 ${EndIf}
 SetOutPath "$INSTDIR"
 File /r "${PAYLOAD}/*"
 WriteUninstaller "$INSTDIR\Uninstall.exe"
 StrCpy $AppIcon "$INSTDIR\nikan.ico"
 IfFileExists "$LOCALAPPDATA\NikanHRM-Test\holding.ico" 0 +2
 StrCpy $AppIcon "$LOCALAPPDATA\NikanHRM-Test\holding.ico"
 CreateDirectory "$SMPROGRAMS\Nikan HRM Test"
 CreateShortcut "$SMPROGRAMS\Nikan HRM Test\Nikan HRM Test.lnk" "$INSTDIR\runtime\pythonw.exe" '-I "$INSTDIR\desktop_test.py"' "$AppIcon"
 CreateShortcut "$SMPROGRAMS\Nikan HRM Test\Close Nikan HRM Test.lnk" "$INSTDIR\runtime\pythonw.exe" '-I "$INSTDIR\stop_hrm_test.py"' "$AppIcon"
 CreateShortcut "$DESKTOP\Nikan HRM Test.lnk" "$INSTDIR\runtime\pythonw.exe" '-I "$INSTDIR\desktop_test.py"' "$AppIcon"
 WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanHRM-Test" "DisplayName" "Nikan HRM Test"
 WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanHRM-Test" "DisplayVersion" "0.15.0"
 WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanHRM-Test" "Publisher" "Nikan Holding"
 WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanHRM-Test" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
 WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanHRM-Test" "InstallLocation" "$INSTDIR"
 WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanHRM-Test" "NoModify" 1
 WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanHRM-Test" "NoRepair" 1
SectionEnd
Function un.onInit
 SetShellVarContext current
 System::Call 'kernel32::OpenMutexW(i 0x100000, i 0, w "Local\NikanHRM-TestDesktop") p .r0'
 ${If} $0 != 0
  System::Call 'kernel32::CloseHandle(p r0)'
  MessageBox MB_ICONSTOP "Close Nikan HRM Test before uninstalling."
  Abort
 ${EndIf}
FunctionEnd
Section "Uninstall"
 Delete "$DESKTOP\Nikan HRM Test.lnk"
 RMDir /r "$SMPROGRAMS\Nikan HRM Test"
 DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanHRM-Test"
 RMDir /r "$INSTDIR"
 ; Never delete LOCALAPPDATA\NikanHRM-Test: business data must survive uninstall.
SectionEnd
