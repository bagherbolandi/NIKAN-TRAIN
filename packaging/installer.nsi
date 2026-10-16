Unicode true
!include "MUI2.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
!ifndef PAYLOAD
 !error "Pass /DPAYLOAD=path to staged application"
!endif
!ifndef OUTPUT
 !define OUTPUT "../distribution/NikanTraining-Setup.exe"
!endif
Var AppIcon
Name "Nikan Training"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\NikanTraining"
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
 Exec '"$INSTDIR\runtime\pythonw.exe" -I "$INSTDIR\desktop.py"'
FunctionEnd
Function .onInit
 StrCpy $INSTDIR "$LOCALAPPDATA\Programs\NikanTraining"
 ${IfNot} ${RunningX64}
  MessageBox MB_ICONSTOP "This installer requires 64-bit Windows 10 or Windows 11."
  Abort
 ${EndIf}
 ${IfNot} ${AtLeastWin10}
  MessageBox MB_ICONSTOP "Windows 10 or Windows 11 is required."
  Abort
 ${EndIf}
 System::Call 'kernel32::OpenMutexW(i 0x100000, i 0, w "Local\NikanTrainingDesktop") p .r0'
 ${If} $0 != 0
  System::Call 'kernel32::CloseHandle(p r0)'
  MessageBox MB_ICONSTOP "Close Nikan Training before installing or upgrading."
  Abort
 ${EndIf}
 SetShellVarContext current
FunctionEnd
Section "Nikan Training" SEC_MAIN
 SetOutPath "$INSTDIR"
 File /r "${PAYLOAD}/*"
 WriteUninstaller "$INSTDIR\Uninstall.exe"
 StrCpy $AppIcon "$INSTDIR\nikan.ico"
 IfFileExists "$LOCALAPPDATA\NikanTraining\holding.ico" 0 +2
 StrCpy $AppIcon "$LOCALAPPDATA\NikanTraining\holding.ico"
 CreateDirectory "$SMPROGRAMS\Nikan Training"
 CreateShortcut "$SMPROGRAMS\Nikan Training\Nikan Training.lnk" "$INSTDIR\runtime\pythonw.exe" '-I "$INSTDIR\desktop.py"' "$AppIcon"
 CreateShortcut "$SMPROGRAMS\Nikan Training\Close Nikan Training.lnk" "$INSTDIR\runtime\pythonw.exe" '-I "$INSTDIR\stop_desktop.py"' "$AppIcon"
 CreateShortcut "$DESKTOP\Nikan Training.lnk" "$INSTDIR\runtime\pythonw.exe" '-I "$INSTDIR\desktop.py"' "$AppIcon"
 WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanTraining" "DisplayName" "Nikan Training"
 WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanTraining" "DisplayVersion" "0.8.0"
 WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanTraining" "Publisher" "Nikan Holding"
 WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanTraining" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
 WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanTraining" "InstallLocation" "$INSTDIR"
 WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanTraining" "NoModify" 1
 WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanTraining" "NoRepair" 1
SectionEnd
Function un.onInit
 SetShellVarContext current
 System::Call 'kernel32::OpenMutexW(i 0x100000, i 0, w "Local\NikanTrainingDesktop") p .r0'
 ${If} $0 != 0
  System::Call 'kernel32::CloseHandle(p r0)'
  MessageBox MB_ICONSTOP "Close Nikan Training before uninstalling."
  Abort
 ${EndIf}
FunctionEnd
Section "Uninstall"
 Delete "$DESKTOP\Nikan Training.lnk"
 RMDir /r "$SMPROGRAMS\Nikan Training"
 DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanTraining"
 RMDir /r "$INSTDIR"
 ; Never delete LOCALAPPDATA\NikanTraining: business data must survive uninstall.
SectionEnd
