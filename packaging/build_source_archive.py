"""Package Nikan HRM 0.12 source and its three verified offline installers.

Build installers first with build_installer.py --test and --network, then run
this script. Importing the module or requesting --help needs no build artifacts.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.12.0'
ARCHIVE_ROOT = f'NikanHRM-{VERSION}'
INSTALLERS = (
    'NikanHRM-Test-Setup.exe',
    'NikanHRM-Server-Setup.exe',
    'NikanTraining-Network-Client-Setup.exe',
)
MANIFESTS = {'test-build-manifest.json': 'test', 'network-build-manifest.json': 'server'}
SOURCE_FOLDERS = ('app', 'network', 'tests', 'packaging', 'docs', '.github', 'resources')
ROOT_PATTERNS = ('*.py', '*.ps1', '*.cmd', '*.sh', '*.nsi', '*.md', 'requirements*.txt', '*.xlsx', '*.png', '*.ico')
EXCLUDED_DIRECTORIES = frozenset({
    '.git', '.venv', 'venv', '__pycache__', '.pytest_cache', '.mypy_cache',
    '.ruff_cache', '.aws', '.codex', '.agents', 'node_modules', 'data', 'backups', 'keys',
})
PRIVATE_SUFFIXES = frozenset({'.key', '.pem', '.pfx', '.p12', '.sqlite', '.sqlite3', '.db', '.nikbak', '.bak'})


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def permitted_source(path, root):
    """Use selected source trees without following links or collecting local state."""
    relative = path.relative_to(root)
    if any(part in EXCLUDED_DIRECTORIES for part in relative.parts):
        return False
    if any((root.joinpath(*relative.parts[:index])).is_symlink() for index in range(1, len(relative.parts) + 1)):
        return False
    name = path.name.lower()
    if name.startswith('.env') or name in {'credentials', 'credentials.json', 'secrets.json', 'session.key', 'server.json'}:
        return False
    if path.suffix.lower() in PRIVATE_SUFFIXES or '.sqlite' in name or name.endswith(('.db-wal', '.db-shm', '.db-journal', '.pyc', '.pyo', '.log')):
        return False
    return path.is_file()


def source_files(root):
    candidates = {root / '.gitignore'}
    for pattern in ROOT_PATTERNS:
        candidates.update(root.glob(pattern))
    for folder in SOURCE_FOLDERS:
        candidates.update((root / folder).rglob('*'))
    return sorted((path for path in candidates if permitted_source(path, root)),
                  key=lambda path: path.relative_to(root).as_posix())


def verified_artifacts(root):
    distribution = root / 'distribution'
    required = [distribution / name for name in INSTALLERS]
    required += [distribution / (name + '.sha256') for name in INSTALLERS]
    required += [distribution / name for name in MANIFESTS]
    missing = [path.relative_to(root).as_posix() for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError('Build --test and --network before making the source archive. Missing: ' + ', '.join(missing))
    if any(path.is_symlink() for path in required):
        raise ValueError('Build artifacts and checksums must be actual files, not symbolic links')
    for name in INSTALLERS:
        installer = distribution / name
        if installer.is_symlink():
            raise ValueError(f'Installer must be an actual file: {name}')
        with installer.open('rb') as stream:
            if stream.read(2) != b'MZ':
                raise ValueError(f'Installer is not a Windows executable: {name}')
        checksum = (distribution / (name + '.sha256')).read_text(encoding='utf-8-sig').split()
        if len(checksum) != 2 or checksum[1].lstrip('*') != name or checksum[0].lower() != sha256(installer):
            raise ValueError(f'Installer SHA-256 or checksum filename mismatch: {name}')
    for name, mode in MANIFESTS.items():
        manifest = json.loads((distribution / name).read_text(encoding='utf-8-sig'))
        if (manifest.get('version') != VERSION or manifest.get('mode') != mode
                or manifest.get('product') != 'Nikan HRM' or not manifest.get('files')):
            raise ValueError(f'Expected Nikan HRM {VERSION} {mode} payload manifest: {name}')
    return sorted(required, key=lambda path: path.name)


def build(root=ROOT, output=None):
    root = Path(root).resolve()
    output = Path(output).resolve() if output else root / 'distribution' / f'NikanHRM-{VERSION}-Source.zip'
    artifacts = verified_artifacts(root)
    files = source_files(root) + artifacts
    relative_paths = {path.relative_to(root).as_posix() for path in files}
    required_source = {'README.md', 'requirements.txt', 'desktop_test.py', 'stop_hrm_test.py',
                       'network_server.py', 'packaging/build_installer.py', 'app/__init__.py'}
    if not required_source.issubset(relative_paths):
        raise ValueError('Required source files missing: ' + ', '.join(sorted(required_source - relative_paths)))
    contents = {'product': 'Nikan HRM', 'version': VERSION,
                'files': {path.relative_to(root).as_posix(): sha256(path) for path in files}}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='nikan-hrm-source-', suffix='.zip.tmp', dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, f'{ARCHIVE_ROOT}/{path.relative_to(root).as_posix()}',
                              compress_type=zipfile.ZIP_STORED if path.suffix.lower() == '.exe' else zipfile.ZIP_DEFLATED)
            archive.writestr(f'{ARCHIVE_ROOT}/SOURCE-CONTENTS.json', json.dumps(contents, indent=2, ensure_ascii=False) + '\n')
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError('Source archive integrity check failed')
            expected = {f'{ARCHIVE_ROOT}/{path}' for path in relative_paths} | {f'{ARCHIVE_ROOT}/SOURCE-CONTENTS.json'}
            if set(archive.namelist()) != expected:
                raise ValueError('Source archive contents do not match selected source and artifacts')
            for relative, expected_hash in contents['files'].items():
                digest = hashlib.sha256()
                with archive.open(f'{ARCHIVE_ROOT}/{relative}') as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(block)
                if digest.hexdigest() != expected_hash:
                    raise ValueError(f'Source or artifact changed while packaging: {relative}')
        temporary.replace(output)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    checksum = output.with_suffix(output.suffix + '.sha256')
    checksum.write_text(f'{sha256(output)}  {output.name}\n', encoding='utf-8')
    print(f'Archive verified: {output} ({output.stat().st_size} bytes; {len(files)} source/artifact files)')
    print(f'SHA-256: {checksum}')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Override distribution/NikanHRM-0.12.0-Source.zip')
    arguments = parser.parse_args()
    build(output=arguments.output)
