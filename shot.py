r"""用 PrintWindow 截某个 Tk 窗口（被挡住也能截）。用法：.venv\Scripts\python.exe shot.py 记录 out.png"""
import ctypes, ctypes.wintypes as W, sys
from PIL import Image
ctypes.windll.shcore.SetProcessDpiAwareness(1)
u, g = ctypes.windll.user32, ctypes.windll.gdi32
u.FindWindowW.restype = W.HWND; u.GetDC.restype = W.HDC; g.CreateCompatibleDC.restype = W.HDC
g.CreateCompatibleDC.argtypes = [W.HDC]; g.CreateCompatibleBitmap.restype = W.HBITMAP; g.CreateCompatibleBitmap.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int]
g.SelectObject.argtypes = [W.HDC, W.HGDIOBJ]; u.PrintWindow.argtypes = [W.HWND, W.HDC, W.UINT]
g.GetBitmapBits.argtypes = [W.HBITMAP, ctypes.c_long, ctypes.c_void_p]
h = u.FindWindowW(None, sys.argv[1]); r = W.RECT(); u.GetWindowRect(h, ctypes.byref(r))
w, hh = r.right - r.left, r.bottom - r.top
dc = u.GetDC(None); m = g.CreateCompatibleDC(dc); b = g.CreateCompatibleBitmap(dc, w, hh); g.SelectObject(m, b)
u.PrintWindow(h, m, 2); buf = ctypes.create_string_buffer(w * hh * 4); g.GetBitmapBits(b, w * hh * 4, buf)
Image.frombuffer('RGBA', (w, hh), buf, 'raw', 'BGRA', 0, 1).convert('RGB').save(sys.argv[2]); print(w, hh)
