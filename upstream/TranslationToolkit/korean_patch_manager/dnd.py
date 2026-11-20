"""Accept files dragged from Explorer onto a Tk window (Windows only, standard library only).

Tk has no built-in file drop. The window is registered with the shell and its
window procedure is wrapped so WM_DROPFILES reaches Python; every other message
goes straight to Tk's own procedure.

The wrapped procedure runs inside Tk's event loop while tkinter has parked the
interpreter state, so it must never call back into tkinter. It only appends the
dropped paths to a list; the caller polls that list from an ``after`` timer.
"""
import os

_hooks = {}       # hwnd -> Tk's own window procedure
_alive = []       # callback thunks are never freed: Windows may still be executing inside one


def enable(window):
    """Return a list that receives one list of paths per drop, or None when drops are unavailable."""
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes
    try:
        user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32
        window.update_idletasks()
        hwnd = int(window.frame(), 16)
        if not hwnd or _hooks.get(hwnd):
            return None
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        for function in (user32.CallWindowProcW, user32.DefWindowProcW):
            function.restype = LRESULT
        user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.SetWindowLongPtrW.restype = ctypes.c_void_p
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        shell32.DragQueryFileW.restype = wintypes.UINT
        shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
        shell32.DragFinish.argtypes = [ctypes.c_void_p]
        shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        drops = []

        def procedure(handle, message, wparam, lparam):
            previous = _hooks.get(handle)
            if message == 0x0233:      # WM_DROPFILES
                try:
                    paths = []
                    for index in range(shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)):
                        length = shell32.DragQueryFileW(wparam, index, None, 0)
                        buffer = ctypes.create_unicode_buffer(length + 1)
                        shell32.DragQueryFileW(wparam, index, buffer, length + 1)
                        paths.append(buffer.value)
                    shell32.DragFinish(wparam)
                    if paths:
                        drops.append(paths)
                except Exception:
                    pass
                return 0
            if message == 0x0082 and previous:      # WM_NCDESTROY: give the window back before it disappears
                _hooks[handle] = None
                user32.SetWindowLongPtrW(handle, -4, previous)
            if previous:
                return user32.CallWindowProcW(previous, handle, message, wparam, lparam)
            return user32.DefWindowProcW(handle, message, wparam, lparam)

        callback = WNDPROC(procedure)
        _alive.append(callback)
        previous = user32.SetWindowLongPtrW(hwnd, -4, ctypes.cast(callback, ctypes.c_void_p))
        if not previous:
            return None
        _hooks[hwnd] = previous
        shell32.DragAcceptFiles(hwnd, True)
        return drops
    except Exception:
        return None
