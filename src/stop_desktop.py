"""Stop only the Nikan desktop instance in the current Windows session."""
import ctypes,os
if __name__=='__main__' and os.name=='nt':
 kernel=ctypes.WinDLL('kernel32',use_last_error=True)
 kernel.OpenEventW.argtypes=[ctypes.c_uint32,ctypes.c_bool,ctypes.c_wchar_p];kernel.OpenEventW.restype=ctypes.c_void_p
 handle=kernel.OpenEventW(0x0002,False,'Local\\NikanTrainingStop')
 if handle:
  kernel.SetEvent(ctypes.c_void_p(handle));kernel.CloseHandle(ctypes.c_void_p(handle))
