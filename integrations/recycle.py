"""Recycle explicitly owned files. Never fall back to permanent deletion."""
import ctypes
import os
import uuid


def recycle(path):
    if os.name != 'nt': raise OSError('此系统尚未提供回收站接口，文件已保留')
    # Use IFileOperation's RECYCLEONDELETE and EARLYFAILURE (Windows 8+).
    # SHFileOperation/ALLOWUNDO alone can silently permanently delete files.
    absolute=str(path.resolve())
    drive=ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor))
    if drive != 3: raise OSError('该位置不支持安全回收（须为本机固定磁盘），文件已保留')
    guid=lambda value:(ctypes.c_ubyte*16).from_buffer_copy(uuid.UUID(value).bytes_le)
    operation,item=ctypes.c_void_p(),ctypes.c_void_p()
    def check(code):
        if code < 0:raise OSError(f'系统回收站操作失败（HRESULT {code & 0xffffffff:08x}），文件已保留')
    def method(pointer,index,*argtypes):
        table=ctypes.cast(pointer,ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.c_long,ctypes.c_void_p,*argtypes)(table[index])
    initialized=ctypes.windll.ole32.CoInitializeEx(None,2)
    check(initialized)
    try:
        check(ctypes.windll.ole32.CoCreateInstance(ctypes.byref(guid('3ad05575-8857-4850-9277-11b85bdb8e09')),None,1,
            ctypes.byref(guid('947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8')),ctypes.byref(operation)))
        check(ctypes.windll.shell32.SHCreateItemFromParsingName(ctypes.c_wchar_p(absolute),None,
            ctypes.byref(guid('43826d1e-e718-42ee-bc55-a1e261c37bfe')),ctypes.byref(item)))
        check(method(operation,5,ctypes.c_uint)(operation,0x80000|0x100000|0x20000000|0x400|0x10|0x4|0x2000))
        check(method(operation,18,ctypes.c_void_p,ctypes.c_void_p)(operation,item,None))
        check(method(operation,21)(operation))
        aborted=ctypes.c_int()
        check(method(operation,22,ctypes.POINTER(ctypes.c_int))(operation,ctypes.byref(aborted)))
        if aborted.value or path.exists():raise OSError('系统回收站未完成操作，请检查文件')
    finally:
        if item:method(item,2)(item)
        if operation:method(operation,2)(operation)
        ctypes.windll.ole32.CoUninitialize()
