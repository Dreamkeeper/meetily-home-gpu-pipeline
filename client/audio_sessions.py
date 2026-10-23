"""Which processes are capturing from a microphone right now, from Windows' Core Audio session API.

Used for call detection. Windows' privacy bookkeeping in the registry (ConsentStore LastUsedTimeStop) stopped
being updated on DKFramework on 2026-10-23 (entries now say PersistedInDatabase=1), so the audio sessions
themselves are the reliable signal: an app in a call holds an *active* capture session on some input device.
Plain ctypes COM, no extra packages.

  python audio_sessions.py      # print the exe names capturing right now
"""
import ctypes
import ctypes.wintypes as w

_ole = ctypes.OleDLL("ole32")
_k32 = ctypes.WinDLL("kernel32")

CLSCTX_ALL = 23
E_CAPTURE = 1
DEVICE_STATE_ACTIVE = 1
SESSION_ACTIVE = 1
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]

    def __init__(self, text):
        super().__init__()
        _ole.CLSIDFromString(ctypes.c_wchar_p("{" + text + "}"), ctypes.byref(self))


CLSID_MMDeviceEnumerator = GUID("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDeviceEnumerator = GUID("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAudioSessionManager2 = GUID("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F")
IID_IAudioSessionControl2 = GUID("BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D")


def _call(obj, index, *args, argtypes=()):
    """Call vtable slot `index` of a COM interface pointer; raises OSError on a failing HRESULT."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtbl[index])
    return fn(obj, *args)


def _release(obj):
    if obj:
        vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])(obj)


def _exe_name(pid):
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf, size = ctypes.create_unicode_buffer(1024), w.DWORD(1024)
        if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1].lower()
        return None
    finally:
        _k32.CloseHandle(h)


def capturing_apps():
    """Lower-case exe names with an active capture session on any active input device."""
    try:
        _ole.CoInitializeEx(None, 0)   # COINIT_MULTITHREADED; harmless if this thread is already initialised
    except OSError:
        pass
    names = set()
    enum = ctypes.c_void_p()
    _ole.CoCreateInstance(ctypes.byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                          ctypes.byref(IID_IMMDeviceEnumerator), ctypes.byref(enum))
    devices = ctypes.c_void_p()
    try:
        # IMMDeviceEnumerator::EnumAudioEndpoints(eCapture, DEVICE_STATE_ACTIVE, &collection)
        _call(enum, 3, E_CAPTURE, DEVICE_STATE_ACTIVE, ctypes.byref(devices),
              argtypes=(ctypes.c_int, w.DWORD, ctypes.POINTER(ctypes.c_void_p)))
        count = ctypes.c_uint()
        _call(devices, 3, ctypes.byref(count), argtypes=(ctypes.POINTER(ctypes.c_uint),))   # GetCount
        for i in range(count.value):
            device, manager, sessions = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
            try:
                _call(devices, 4, i, ctypes.byref(device), argtypes=(ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)))
                # IMMDevice::Activate(IID_IAudioSessionManager2) -> IAudioSessionManager2::GetSessionEnumerator
                _call(device, 3, ctypes.byref(IID_IAudioSessionManager2), CLSCTX_ALL, None, ctypes.byref(manager),
                      argtypes=(ctypes.POINTER(GUID), w.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)))
                _call(manager, 5, ctypes.byref(sessions), argtypes=(ctypes.POINTER(ctypes.c_void_p),))
                n = ctypes.c_int()
                _call(sessions, 3, ctypes.byref(n), argtypes=(ctypes.POINTER(ctypes.c_int),))
                for j in range(n.value):
                    ctl, ctl2 = ctypes.c_void_p(), ctypes.c_void_p()
                    try:
                        _call(sessions, 4, j, ctypes.byref(ctl), argtypes=(ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)))
                        state = ctypes.c_int()
                        _call(ctl, 3, ctypes.byref(state), argtypes=(ctypes.POINTER(ctypes.c_int),))   # GetState
                        if state.value != SESSION_ACTIVE:
                            continue
                        _call(ctl, 0, ctypes.byref(IID_IAudioSessionControl2), ctypes.byref(ctl2),
                              argtypes=(ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)))   # QueryInterface
                        pid = w.DWORD()
                        _call(ctl2, 14, ctypes.byref(pid), argtypes=(ctypes.POINTER(w.DWORD),))   # GetProcessId
                        name = _exe_name(pid.value) if pid.value else None
                        if name:
                            names.add(name)
                    except OSError:
                        continue   # e.g. a session that went away meanwhile
                    finally:
                        _release(ctl2)
                        _release(ctl)
            except OSError:
                continue
            finally:
                _release(sessions)
                _release(manager)
                _release(device)
    finally:
        _release(devices)
        _release(enum)
    return names


if __name__ == "__main__":
    print(sorted(capturing_apps()) or "nothing is capturing from a microphone")
