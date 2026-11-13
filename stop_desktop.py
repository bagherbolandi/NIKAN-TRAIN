"""Stop only the Nikan desktop instance in the current Windows session."""
import ctypes,os
def stop(profile='NikanTraining'):
 if profile not in ('NikanTraining','NikanHRM-Test'):raise ValueError('Unsupported desktop profile')
 if os.name!='nt':return
 kernel=ctypes.WinDLL('kernel32',use_last_error=True)
 kernel.OpenEventW.argtypes=[ctypes.c_uint32,ctypes.c_bool,ctypes.c_wchar_p];kernel.OpenEventW.restype=ctypes.c_void_p
 handle=kernel.OpenEventW(0x0002,False,'Local\\'+profile+'Stop')
 if handle:
  kernel.SetEvent(ctypes.c_void_p(handle));kernel.CloseHandle(ctypes.c_void_p(handle))
if __name__=='__main__':stop()
