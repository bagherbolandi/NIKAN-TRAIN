"""Drop-in replacement for build_release.py (0.11.2) — no PowerShell at install time.

Changes vs. the original builder:
  * The whole installer-preflight.ps1 / nsExec::ExecToStack block is removed.
    desktop.py already guards a second instance with CreateMutexW, so the
    PowerShell preflight was redundant. Removing it also removes WMI, locked-file
    and AppLocker/AV dependencies that made installation machine-specific.
  * Server uninstall no longer calls powershell.exe; it removes the two tasks and
    the firewall rule with schtasks.exe / netsh.exe instead.
  * VIProductVersion / VIAddVersionKey added (version resource helps SmartScreen).
  * Shortcut "Nikan Password Recovery" renamed to "Nikan Account Management".

Run on the build machine (Windows):
    python build_release_fixed.py --nsis C:\\path\\to\\makensis.exe --output C:\\path\\to\\outputs
Then sign every produced EXE with an Authenticode certificate (see README.md).
"""
import argparse, hashlib, json, subprocess, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = '0.11.2'
FILE_VERSION = '0.11.2.0'


def files():
    roots = ['app', 'resources', 'runtime', 'database']
    result = []
    for directory in roots:
        for p in (ROOT / directory).rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo'):
                result.append(p)
    result += [ROOT / n for n in [
        'desktop.py', 'stop_desktop.py', 'server.py', 'install-server.ps1',
        'initialize-server.ps1', 'unregister-server.ps1', 'README.fa.md',
        'requirements.txt', 'nikan.ico', 'PROVENANCE.json',
        'installer-preflight.ps1', 'recover_account.py']]
    return sorted(result)


def build(nsis, out):
    out.mkdir(parents=True, exist_ok=True)
    payload = files()
    for mode in ['Desktop', 'Server']:
        server = mode == 'Server'
        name = f'NikanTraining-{mode}-{VERSION}'
        destination = '$PROGRAMFILES64\\NikanTrainingServer' if server else '$LOCALAPPDATA\\Programs\\NikanTraining'
        registry = 'HKLM' if server else 'HKCU'
        folder = 'Nikan Training Server' if server else 'Nikan Training'
        lines = [
            'Unicode True', '!include "MUI2.nsh"',
            f'Name "Nikan Training {mode} {VERSION}"',
            f'OutFile "{out / name}-Setup.exe"',
            f'InstallDir "{destination}"',
            f'RequestExecutionLevel {"admin" if server else "user"}',
            'SetCompressor /SOLID lzma',
            # Version resource: without it SmartScreen has no publisher context.
            f'VIProductVersion "{FILE_VERSION}"',
            'VIAddVersionKey /LANG=1033 "CompanyName" "Nikan Holding"',
            f'VIAddVersionKey /LANG=1033 "FileDescription" "Nikan Training {mode} Installer"',
            f'VIAddVersionKey /LANG=1033 "FileVersion" "{FILE_VERSION}"',
            'VIAddVersionKey /LANG=1033 "ProductName" "Nikan Training"',
            f'VIAddVersionKey /LANG=1033 "ProductVersion" "{VERSION}"',
            'VIAddVersionKey /LANG=1033 "LegalCopyright" "Copyright (c) Nikan Holding"',
            f'VIAddVersionKey /LANG=1033 "OriginalFilename" "{name}-Setup.exe"',
            '!insertmacro MUI_PAGE_WELCOME', '!insertmacro MUI_PAGE_DIRECTORY',
            '!insertmacro MUI_PAGE_INSTFILES', '!insertmacro MUI_PAGE_FINISH',
            '!insertmacro MUI_UNPAGE_CONFIRM', '!insertmacro MUI_UNPAGE_INSTFILES',
            '!insertmacro MUI_LANGUAGE "English"',
            'Section "Install"',
            f'SetShellVarContext {"all" if server else "current"}', 'SetRegView 64',
            # NOTE: no InitPluginsDir / PowerShell preflight. desktop.py owns the
            # single-instance mutex; NSIS itself reports any genuinely locked file.
        ]
        previous = None
        for p in payload:
            rel = p.relative_to(ROOT)
            parent = str(rel.parent).replace('/', '\\')
            target = '$INSTDIR' + ('\\' + parent if parent != '.' else '')
            if target != previous:
                lines.append(f'SetOutPath "{target}"')
                previous = target
            lines.append(f'File "{p}"')
        lines += ['SetOutPath "$INSTDIR"', f'CreateDirectory "$SMPROGRAMS\\{folder}"']
        if server:
            lines += [
                f'''CreateShortcut "$SMPROGRAMS\\{folder}\\Initialize Nikan Server.lnk" "$SYSDIR\\WindowsPowerShell\\v1.0\\powershell.exe" '-NoProfile -NoExit -File "$INSTDIR\\initialize-server.ps1"' "$INSTDIR\\nikan.ico"''',
                f'''CreateShortcut "$SMPROGRAMS\\{folder}\\Register Nikan Server Startup.lnk" "$SYSDIR\\WindowsPowerShell\\v1.0\\powershell.exe" '-NoProfile -NoExit -File "$INSTDIR\\install-server.ps1"' "$INSTDIR\\nikan.ico"''',
            ]
        else:
            lines += [
                f'''CreateShortcut "$SMPROGRAMS\\{folder}\\Nikan Training.lnk" "$INSTDIR\\runtime\\pythonw.exe" '"$INSTDIR\\desktop.py"' "$INSTDIR\\nikan.ico"''',
                f'''CreateShortcut "$DESKTOP\\Nikan Training.lnk" "$INSTDIR\\runtime\\pythonw.exe" '"$INSTDIR\\desktop.py"' "$INSTDIR\\nikan.ico"''',
            ]
        recovery_args = '"$INSTDIR\\recover_account.py"' + (' --server' if server else '')
        lines.append(f'''CreateShortcut "$SMPROGRAMS\\{folder}\\Nikan Account Management.lnk" "$INSTDIR\\runtime\\python.exe" '{recovery_args}' "$INSTDIR\\nikan.ico"''')
        key = f'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\NikanTraining{mode}'
        lines += [
            f'CreateShortcut "$SMPROGRAMS\\{folder}\\Installation Guide.lnk" "$INSTDIR\\README.fa.md"',
            'WriteUninstaller "$INSTDIR\\Uninstall.exe"',
            f'WriteRegStr {registry} "{key}" "DisplayName" "Nikan Training {mode}"',
            f'WriteRegStr {registry} "{key}" "DisplayVersion" "{VERSION}"',
            f'''WriteRegStr {registry} "{key}" "UninstallString" '"$INSTDIR\\Uninstall.exe"' ''',
            'SectionEnd', 'Section "Uninstall"',
            f'SetShellVarContext {"all" if server else "current"}', 'SetRegView 64',
        ]
        if server:
            # Native binaries instead of powershell.exe -ExecutionPolicy Bypass.
            lines += [
                '''nsExec::ExecToLog '"$SYSDIR\\schtasks.exe" /Delete /TN "NikanTrainingServer" /F' ''', 'Pop $0',
                '''nsExec::ExecToLog '"$SYSDIR\\schtasks.exe" /Delete /TN "NikanTrainingBackup" /F' ''', 'Pop $0',
                '''nsExec::ExecToLog '"$SYSDIR\\netsh.exe" advfirewall firewall delete rule name="NikanTrainingIntranet"' ''', 'Pop $0',
            ]
        for p in payload:
            lines.append('Delete "$INSTDIR\\' + str(p.relative_to(ROOT)).replace('/', '\\') + '"')
        lines += ['Delete "$INSTDIR\\Uninstall.exe"']
        directories = set()
        for p in payload:
            parent = p.relative_to(ROOT).parent
            while str(parent) != '.':
                directories.add(parent)
                parent = parent.parent
        lines += ['RMDir "$INSTDIR\\' + str(p).replace('/', '\\') + '"'
                  for p in sorted(directories, key=lambda p: len(p.parts), reverse=True)]
        lines += ['RMDir "$INSTDIR"', f'Delete "$SMPROGRAMS\\{folder}\\*.lnk"', f'RMDir "$SMPROGRAMS\\{folder}"']
        if not server:
            lines.append('Delete "$DESKTOP\\Nikan Training.lnk"')
        lines += [f'DeleteRegKey {registry} "{key}"', 'SectionEnd']
        script = out / (name + '.nsi')
        script.write_text('\n'.join(lines), encoding='utf-8-sig')
        subprocess.run([str(nsis), '/V2', str(script)], check=True)
    with zipfile.ZipFile(out / f'NikanTraining-Server-{VERSION}-Offline.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for p in payload:
            z.write(p, 'NikanTrainingServer/' + p.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(out / f'NikanTraining-{VERSION}-Source.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for p in payload:
            if p.relative_to(ROOT).parts[0] != 'runtime':
                z.write(p, 'NikanTraining/' + p.relative_to(ROOT).as_posix())
        for n in ['build_release.py', 'test_governance.py', 'test_recovery.py', 'qa-browser.cjs', 'VALIDATION.md']:
            z.write(ROOT / n, 'NikanTraining/' + n)
    checksums = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(out.iterdir()) if p.suffix in ('.exe', '.zip')}
    (out / 'SHA256SUMS.txt').write_text('\n'.join(v + '  ' + k for k, v in checksums.items()) + '\n', encoding='ascii')
    print(json.dumps(checksums, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--nsis', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.nsis.resolve(), args.output.resolve())
