"""Native Windows named-object isolation; the NSIS guard uses the same names."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Requires real Windows kernel named mutexes/events')
def test_native_windows_profiles_single_instance_and_independent_stop(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, 'LOCALAPPDATA': str(tmp_path), 'NIKAN_HEADLESS_DESKTOP': '1'}
    env.pop('NIKAN_DESKTOP_PROFILE', None)
    processes = {}
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenMutexW.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.OpenMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    try:
        for profile, script in [('NikanTraining', 'desktop.py'), ('NikanHRM-Test', 'desktop_test.py')]:
            process = subprocess.Popen([sys.executable, '-I', str(root / script)], cwd=tmp_path, env=env)
            processes[profile] = process
            endpoint = tmp_path / profile / 'desktop-endpoint.json'
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                assert process.poll() is None, (profile, 'desktop exited before readiness')
                if endpoint.exists():
                    assert json.loads(endpoint.read_text())['url'].startswith('http://127.0.0.1:')
                    break
                time.sleep(.1)
            else:
                pytest.fail('No desktop endpoint: ' + profile)
            handle = kernel.OpenMutexW(0x100000, False, 'Local\\' + profile + 'Desktop')
            assert handle, (profile, 'mutex not visible under the namespace checked by NSIS')
            kernel.CloseHandle(handle)
        second = subprocess.run([sys.executable, '-I', str(root / 'desktop_test.py')], cwd=tmp_path, env=env, timeout=15)
        assert second.returncode == 0
        assert processes['NikanHRM-Test'].poll() is None
        subprocess.run([sys.executable, '-I', str(root / 'stop_hrm_test.py')], cwd=tmp_path, env=env, check=True, timeout=10)
        assert processes['NikanHRM-Test'].wait(timeout=15) == 0
        assert not (tmp_path / 'NikanHRM-Test' / 'desktop-endpoint.json').exists()
        assert processes['NikanTraining'].poll() is None, 'Test stop reached original desktop'
        subprocess.run([sys.executable, '-I', str(root / 'stop_desktop.py')], cwd=tmp_path, env=env, check=True, timeout=10)
        assert processes['NikanTraining'].wait(timeout=15) == 0
        assert not (tmp_path / 'NikanTraining' / 'desktop-endpoint.json').exists()
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
