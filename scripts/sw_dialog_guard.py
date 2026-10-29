"""SolidWorks 模态对话框看门狗。

用途：自动化跑批时，SolidWorks 偶尔会弹出「新建 SOLIDWORKS 文件」模板选择框、
「输入诊断」「重建错误」等模态对话框，COM 调用会被卡住，必须人工点确定。
本模块起一个后台线程，轮询顶层窗口，命中目标对话框就发送 IDOK / 关闭。

用法：
    from sw_dialog_guard import DialogGuard
    with DialogGuard():
        ...  # 你的 COM 操作
"""
import threading
import time

try:
    import win32gui
    import win32con
    import win32process
except Exception:  # pragma: no cover
    win32gui = None

# 标题关键字（中英文都覆盖）
TITLE_KEYWORDS = (
    "新建 SOLIDWORKS 文件",
    "新建 SolidWorks 文件",
    "New SOLIDWORKS Document",
    "新建文件",
    "输入诊断",
    "Input Diagnostics",
    "重建模型错误",
    "Rebuild Errors",
)

# 需要主动点掉但不该按 IDOK 的（点“取消/关闭”更安全）
CANCEL_KEYWORDS = ("输入诊断", "Input Diagnostics", "重建模型错误", "Rebuild Errors")


class DialogGuard:
    def __init__(self, interval=0.4, verbose=True, title_keywords=None,
                 message_keywords=(), pid=None):
        """
        @param title_keywords 标题关键字；None 使用模块默认列表。
        @param message_keywords 正文关键字；非空时必须命中对话框内文本才点击，
               用于避免仅凭标题 “SOLIDWORKS” 误点未知确认框。
        @param pid 只处理该进程（int）或这些进程（iterable）的对话框；None 不限制。
        """
        self.interval = interval
        self.verbose = verbose
        self.title_keywords = tuple(title_keywords) if title_keywords is not None else TITLE_KEYWORDS
        self.message_keywords = tuple(message_keywords)
        self.pid = pid
        self._stop = threading.Event()
        self._thread = None
        self.killed = []

    def _pid_allowed(self, hwnd):
        if self.pid is None:
            return True
        try:
            _tid, pid = win32gui.GetWindowThreadProcessId(hwnd)
        except Exception:
            return False
        allowed = (self.pid,) if isinstance(self.pid, int) else tuple(self.pid)
        return int(pid) in {int(item) for item in allowed}

    @staticmethod
    def _dialog_text(hwnd):
        parts = []

        def cb(child, _):
            try:
                if win32gui.IsWindowVisible(child):
                    text = (win32gui.GetWindowText(child) or "").strip()
                    if text:
                        parts.append(text)
            except Exception:
                pass

        try:
            win32gui.EnumChildWindows(hwnd, cb, None)
        except Exception:
            pass
        return "\n".join(parts)

    def _message_matches(self, hwnd):
        if not self.message_keywords:
            return True
        text = self._dialog_text(hwnd)
        return any(keyword in text for keyword in self.message_keywords)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    def start(self):
        if win32gui is None:
            if self.verbose:
                print("[guard] 缺少 pywin32，看门狗未启用")
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        if self.verbose:
            print("[guard] 已启动")

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._sweep()
            except Exception:
                pass
            time.sleep(self.interval)

    def _sweep(self):
        targets = []

        def cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            cls = win32gui.GetClassName(hwnd)
            if cls != "#32770":       # 标准对话框类
                return
            title = win32gui.GetWindowText(hwnd) or ""
            if not any(k in title for k in self.title_keywords):
                return
            if not self._pid_allowed(hwnd) or not self._message_matches(hwnd):
                return
            targets.append((hwnd, title))

        win32gui.EnumWindows(cb, None)
        for hwnd, title in targets:
            cancel = any(k in title for k in CANCEL_KEYWORDS)
            if cancel:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                action = "关闭"
            else:
                # 先找按钮直接 BM_CLICK（跨进程最可靠）；找不到再退回 WM_COMMAND IDOK
                btn = self._find_button(hwnd)
                if btn:
                    win32gui.PostMessage(btn, win32con.BM_CLICK, 0, 0)
                else:
                    win32gui.PostMessage(hwnd, win32con.WM_COMMAND, 1, 0)
                action = "确定"
            self.killed.append((title, action))
            if self.verbose:
                print(f"[guard] {action} 对话框: {title}")

    @staticmethod
    def _find_button(hwnd):
        """在对话框里找 IDOK / 文本为“确定/OK”的按钮"""
        found = []

        def cb(child, _):
            if win32gui.GetClassName(child) != "Button":
                return
            cid = win32gui.GetDlgCtrlID(child)
            text = (win32gui.GetWindowText(child) or "").replace("&", "").strip()
            if cid == 1 or text in ("确定", "OK", "是", "Yes"):
                found.append(child)

        win32gui.EnumChildWindows(hwnd, cb, None)
        return found[0] if found else None


def ensure_templates(sw):
    """把三种默认模板写进用户偏好，从源头避免弹模板选择框。"""
    import glob
    import os

    base = r"C:\ProgramData\SolidWorks\SOLIDWORKS *\templates"
    pats = {"part": "*.prtdot", "assembly": "*.asmdot", "drawing": "*.drwdot"}
    prefs = {"part": 8, "assembly": 9, "drawing": 10}  # swDefaultTemplatePart/Assembly/Drawing
    out = {}
    for kind, pat in pats.items():
        cands = []
        for root in glob.glob(base):
            cands.extend(glob.glob(os.path.join(root, pat)))
        if not cands:
            continue
        # 优先国标模板
        cands.sort(key=lambda p: (0 if "gb_" in os.path.basename(p).lower() else 1, p))
        try:
            sw.SetUserPreferenceStringValue(prefs[kind], cands[0])
            out[kind] = cands[0]
        except Exception as exc:
            print(f"[guard] 设置 {kind} 模板失败: {exc}")
    try:
        # swUserPreferenceToggle_e.swAlwaysUseDefaultTemplates = 111
        sw.SetUserPreferenceToggle(111, True)
    except Exception:
        pass
    return out
