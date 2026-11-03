"""Resource budgets only: profiles never change media or recognition content."""
import ctypes
import os

PROFILES = {'low': (1, 1), 'balanced': (4, 2), 'high': (8, 4)}


def available_memory():
    if os.name != 'nt':
        try:
            return os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
        except (ValueError, OSError, AttributeError):
            return None
    class Memory(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                    *[(name, ctypes.c_ulonglong) for name in
                      ('total', 'available', 'page_total', 'page_available', 'virtual_total', 'virtual_available', 'extended')]]
    state = Memory()
    state.length = ctypes.sizeof(state)
    return state.available if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)) else None


def resolve(profile='auto', cpu=None, memory=None):
    if profile not in ('auto', *PROFILES):
        raise ValueError('未知处理速度档位')
    cpu = max(1, cpu or os.cpu_count() or 1)
    memory = available_memory() if memory is None else memory
    gib = memory / 1024**3 if memory is not None else 2
    capacity = 'low' if cpu < 4 or gib < 2 else 'balanced' if cpu < 12 or gib < 8 else 'high'
    chosen = capacity if profile == 'auto' else profile
    decode, neural = PROFILES[chosen]
    memory_cap = PROFILES[capacity]
    return {'requested': profile, 'effective': chosen, 'cpu_count': cpu, 'available_memory': memory,
            'decode_threads': min(decode, cpu, memory_cap[0]),
            'ocr_threads': min(neural, max(1, cpu//2), memory_cap[1]),
            'asr_threads': min(neural, max(1, cpu//2), memory_cap[1])}


def for_job(a, jid):
    return a.job_by_id(jid).get('resources') or {'decode_threads': 2, 'ocr_threads': 2, 'asr_threads': 2}
