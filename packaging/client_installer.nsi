Unicode true
!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
Name "Nikan Training Network Client"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\NikanTrainingNetworkClient"
RequestExecutionLevel user
SetCompressor /SOLID lzma
!define MUI_ICON "nikan.ico"
Var ServerURL
Var URLInput
!insertmacro MUI_PAGE_WELCOME
Page custom AddressPage AddressLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Farsi"
!insertmacro MUI_LANGUAGE "English"
Function .onInit
 StrCpy $ServerURL "https://YOUR-SERVER:8766"
 ${GetParameters} $0
 ; Read one /URL= token without treating the slashes in https:// as option delimiters.
 StrCpy $1 0
 FindURL:
 StrCpy $2 $0 5 $1
 StrCmp $2 "/URL=" ReadURLStart
 StrCmp $2 "" ArgsDone
 IntOp $1 $1 + 1
 Goto FindURL
 ReadURLStart:
 IntOp $1 $1 + 5
 StrCpy $ServerURL ""
 StrCpy $3 0
 StrCpy $2 $0 1 $1
 StrCmp $2 '$\"' 0 ReadURL
 StrCpy $3 1
 IntOp $1 $1 + 1
 ReadURL:
 StrCpy $2 $0 1 $1
 StrCmp $2 "" ArgsDone
 ${If} $3 == 1
  StrCmp $2 '$\"' ArgsDone
 ${Else}
  StrCmp $2 " " ArgsDone
  StrCmp $2 '$\t' ArgsDone
 ${EndIf}
 StrCpy $ServerURL "$ServerURL$2"
 IntOp $1 $1 + 1
 Goto ReadURL
 ArgsDone:
 ClearErrors
FunctionEnd
Function AddressPage
 nsDialogs::Create 1018
 Pop $0
 ${NSD_CreateLabel} 0 0 100% 42u "آدرس HTTPS سرور نیکان را وارد کنید. گواهی سرور باید توسط مدیر شبکه در Windows مورد اعتماد قرار گیرد. این نصب داده‌های مستقل ایجاد نمی‌کند."
 Pop $0
 ${NSD_CreateText} 0 52u 100% 16u "$ServerURL"
 Pop $URLInput
 nsDialogs::Show
FunctionEnd
Function AddressLeave
 ${NSD_GetText} $URLInput $ServerURL
 StrCpy $0 $ServerURL 8
 ${If} $0 != "https://"
  MessageBox MB_ICONSTOP "HTTPS address is required."
  Abort
 ${EndIf}
FunctionEnd
Section "Network Client"
 StrCpy $0 $ServerURL 8
 ${If} $0 != "https://"
  SetErrorLevel 1
  Abort "HTTPS address is required."
 ${EndIf}
 SetOutPath "$INSTDIR"
 File "nikan.ico"
 WriteUninstaller "$INSTDIR\Uninstall.exe"
 CreateDirectory "$SMPROGRAMS\Nikan Training Network"
 WriteINIStr "$SMPROGRAMS\Nikan Training Network\Nikan Training.url" "InternetShortcut" "URL" "$ServerURL"
 WriteINIStr "$DESKTOP\Nikan Training Network.url" "InternetShortcut" "URL" "$ServerURL"
 WriteINIStr "$DESKTOP\Nikan Training Network.url" "InternetShortcut" "IconFile" "$INSTDIR\nikan.ico"
 WriteINIStr "$DESKTOP\Nikan Training Network.url" "InternetShortcut" "IconIndex" "0"
 WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanTrainingNetworkClient" "DisplayName" "Nikan Training Network Client"
 WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanTrainingNetworkClient" "DisplayVersion" "0.5.0"
 WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanTrainingNetworkClient" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
SectionEnd
Section "Uninstall"
 Delete "$DESKTOP\Nikan Training Network.url"
 Delete "$SMPROGRAMS\Nikan Training Network\Nikan Training.url"
 RMDir "$SMPROGRAMS\Nikan Training Network"
 DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\NikanTrainingNetworkClient"
 RMDir /r "$INSTDIR"
SectionEnd
