"""Nikan HRM 0.12.0 — Windows installer builder (no install-time PowerShell).

Derived from build_release_fixed.py (0.11.2) with the same no-PowerShell install,
version resource and native-Task-Scheduler uninstall, plus the HRM modules,
documents and tests in both payloads.

On the Windows build machine (Python 3.13 + NSIS 3 + bundled runtime/):

    python build_hrm.py --nsis "C:\\Program Files (x86)\\NSIS\\makensis.exe" --output C:\\build\\out

Linux/CI check (writes .nsi + Source.zip + Offline.zip + SHA256SUMS.txt, no EXE):

    python build_hrm.py --check --output /tmp/hrm-build

Then sign EVERY produced EXE with Authenticode and publish SHA256SUMS.txt.
"""
import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = '0.12.0'
FILE_VERSION = '0.12.0.0'
PRODUCT = 'Nikan HRM'

ROOT_FILES = [
    'desktop.py', 'stop_desktop.py', 'server.py', 'install-server.ps1',
    'initialize-server.ps1', 'unregister-server.ps1', 'README.fa.md',
    'requirements.txt', 'nikan.ico', 'PROVENANCE.json', 'recover_account.py',
    'build_hrm.py', 'test_hrm.py', 'VALIDATION.md', 'qa-browser.cjs',
    'test_governance.py', 'test_recovery.py', 'qa-hrm-ui.cjs',
]


def files():
    result = []
    for directory in ['app', 'resources', 'runtime', 'database', 'docs']:
        for path in (ROOT / directory).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix not in ('.pyc', '.pyo'):
                result.append(path)
    for name in ROOT_FILES:
        path = ROOT / name
        if path.is_file():
            result.append(path)
    return sorted(set(result))


def build(nsis=None, out=None, check=False):
    out.mkdir(parents=True, exist_ok=True)
    payload = files()
    for mode in ['Desktop', 'Server']:
        server = mode == 'Server'
        name = f'NikanHRM-{mode}-{VERSION}'
        destination = '$PROGRAMFILES64\\NikanHRMServer' if server else '$LOCALAPPDATA\\Programs\\NikanHRM'
        registry = 'HKLM' if server else 'HKCU'
        folder = 'Nikan HRM Server' if server else 'Nikan HRM'
        lines = [
            'Unicode True', '!include "MUI2.nsh"',
            f'Name "{PRODUCT} {mode} {VERSION}"',
            f'OutFile "{out / name}-Setup.exe"',
            f'InstallDir "{destination}"',
            f'RequestExecutionLevel {"admin" if server else "user"}',
            'SetCompressor /SOLID lzma',
            f'VIProductVersion "{FILE_VERSION}"',
            'VIAddVersionKey /LANG=1033 "CompanyName" "Nikan Holding"',
            f'VIAddVersionKey /LANG=1033 "FileDescription" "{PRODUCT} {mode} Installer"',
            f'VIAddVersionKey /LANG=1033 "FileVersion" "{FILE_VERSION}"',
            f'VIAddVersionKey /LANG=1033 "ProductName" "{PRODUCT}"',
            f'VIAddVersionKey /LANG=1033 "ProductVersion" "{VERSION}"',
            'VIAddVersionKey /LANG=1033 "LegalCopyright" "Copyright (c) Nikan Holding"',
            f'VIAddVersionKey /LANG=1033 "OriginalFilename" "{name}-Setup.exe"',
            '!insertmacro MUI_PAGE_WELCOME', '!insertmacro MUI_PAGE_DIRECTORY',
            '!insertmacro MUI_PAGE_INSTFILES', '!insertmacro MUI_PAGE_FINISH',
            '!insertmacro MUI_UNPAGE_CONFIRM', '!insertmacro MUI_UNPAGE_INSTFILES',
            '!insertmacro MUI_LANGUAGE "English"',
            'Section "Install"',
            f'SetShellVarContext {"all" if server else "current"}', 'SetRegView 64',
            # No InitPluginsDir / installer-preflight.ps1 / nsExec at install time.
            # desktop.py owns the single-instance mutex; an AV/EDR blocking
            # powershell.exe therefore cannot abort installation.
        ]
        previous = None
        for path in payload:
            rel = path.relative_to(ROOT)
            parent = str(rel.parent).replace('/', '\\')
            target = '$INSTDIR' + ('\\' + parent if parent != '.' else '')
            if target != previous:
                lines.append(f'SetOutPath "{target}"')
                previous = target
            lines.append(f'File "{path}"')
        lines += ['SetOutPath "$INSTDIR"', f'CreateDirectory "$SMPROGRAMS\\{folder}"']
        if server:
            lines += [
                f'''CreateShortcut "$SMPROGRAMS\\{folder}\\Initialize Nikan HRM Server.lnk" "$SYSDIR\\WindowsPowerShell\\v1.0\\powershell.exe" '-NoProfile -NoExit -File "$INSTDIR\\initialize-server.ps1"' "$INSTDIR\\nikan.ico"''',
                f'''CreateShortcut "$SMPROGRAMS\\{folder}\\Register Nikan HRM Startup.lnk" "$SYSDIR\\WindowsPowerShell\\v1.0\\powershell.exe" '-NoProfile -NoExit -File "$INSTDIR\\install-server.ps1"' "$INSTDIR\\nikan.ico"''',
            ]
        else:
            lines += [
                f'''CreateShortcut "$SMPROGRAMS\\{folder}\\Nikan HRM.lnk" "$INSTDIR\\runtime\\pythonw.exe" '"$INSTDIR\\desktop.py"' "$INSTDIR\\nikan.ico"''',
                f'''CreateShortcut "$DESKTOP\\Nikan HRM.lnk" "$INSTDIR\\runtime\\pythonw.exe" '"$INSTDIR\\desktop.py"' "$INSTDIR\\nikan.ico"''',
            ]
        recovery_args = '"$INSTDIR\\recover_account.py"' + (' --server' if server else '')
        lines.append(f'''CreateShortcut "$SMPROGRAMS\\{folder}\\Nikan Account Management.lnk" "$INSTDIR\\runtime\\python.exe" '{recovery_args}' "$INSTDIR\\nikan.ico"''')
        key = f'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\NikanHRM{mode}'
        lines += [
            f'CreateShortcut "$SMPROGRAMS\\{folder}\\Installation Guide.lnk" "$INSTDIR\\README.fa.md"',
            'WriteUninstaller "$INSTDIR\\Uninstall.exe"',
            f'WriteRegStr {registry} "{key}" "DisplayName" "{PRODUCT} {mode}"',
            f'WriteRegStr {registry} "{key}" "DisplayVersion" "{VERSION}"',
            f'''WriteRegStr {registry} "{key}" "UninstallString" '"$INSTDIR\\Uninstall.exe"' ''',
            'SectionEnd', 'Section "Uninstall"',
            f'SetShellVarContext {"all" if server else "current"}', 'SetRegView 64',
        ]
        if server:
            lines += [
                '''nsExec::ExecToLog '"$SYSDIR\\schtasks.exe" /Delete /TN "NikanTrainingServer" /F' ''', 'Pop $0',
                '''nsExec::ExecToLog '"$SYSDIR\\schtasks.exe" /Delete /TN "NikanTrainingBackup" /F' ''', 'Pop $0',
                '''nsExec::ExecToLog '"$SYSDIR\\netsh.exe" advfirewall firewall delete rule name="NikanTrainingIntranet"' ''', 'Pop $0',
            ]
        for path in payload:
            lines.append('Delete "$INSTDIR\\' + str(path.relative_to(ROOT)).replace('/', '\\') + '"')
        lines += ['Delete "$INSTDIR\\Uninstall.exe"']
        directories = set()
        for path in payload:
            parent = path.relative_to(ROOT).parent
            while str(parent) != '.':
                directories.add(parent)
                parent = parent.parent
        lines += ['RMDir "$INSTDIR\\' + str(p).replace('/', '\\') + '"'
                  for p in sorted(directories, key=lambda p: len(p.parts), reverse=True)]
        lines += ['RMDir "$INSTDIR"', f'Delete "$SMPROGRAMS\\{folder}\\*.lnk"',
                  f'RMDir "$SMPROGRAMS\\{folder}"']
        if not server:
            lines.append('Delete "$DESKTOP\\Nikan HRM.lnk"')
        lines += [f'DeleteRegKey {registry} "{key}"', 'SectionEnd']
        script = out / (name + '.nsi')
        script.write_text('\n'.join(lines), encoding='utf-8-sig')
        if check:
            print(f'[check] wrote {script}')
        else:
            subprocess.run([str(nsis), '/V2', str(script)], check=True)
    with zipfile.ZipFile(out / f'NikanHRM-Server-{VERSION}-Offline.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in payload:
            archive.write(path, 'NikanHRMServer/' + path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(out / f'NikanHRM-{VERSION}-Source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in payload:
            if path.relative_to(ROOT).parts[0] != 'runtime':
                archive.write(path, 'NikanHRM/' + path.relative_to(ROOT).as_posix())
    checksums = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(out.iterdir()) if p.suffix in ('.exe', '.zip')}
    (out / 'SHA256SUMS.txt').write_text(
        '\n'.join(v + '  ' + k for k, v in checksums.items()) + '\n', encoding='ascii')
    print(json.dumps(checksums, indent=2))
    return checksums


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--nsis', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--check', action='store_true', help='Generate scripts/packages without makensis (Linux/CI)')
    args = parser.parse_args()
    if not args.check and args.nsis is None:
        parser.error('--nsis is required unless --check is used')
    build(args.nsis.resolve() if args.nsis else None, args.output.resolve(), args.check)
