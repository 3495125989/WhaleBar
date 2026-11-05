"""全局快捷键（Carbon RegisterEventHotKey）。

用 Carbon 的旧接口，好处是**不需要辅助功能权限**，登记完就能用。
（NSEvent 的全局监听要用户去开"辅助功能"，太重。）

用法：
    hk = GlobalHotkeys(log=print)
    hk.register(0x01, 0x0100 | 0x0800, "开字幕", callback)   # ⌘⌥S
    hk.install()
"""

import ctypes

carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")

kEventClassKeyboard = 0x6B657962      # 'keyb'
kEventHotKeyPressed = 5
kEventParamDirectObject = 0x2D2D2D2D  # '----'
typeEventHotKeyID = 0x686B6964        # 'hkid'

cmdKey = 0x0100
shiftKey = 0x0200
optionKey = 0x0800
controlKey = 0x1000

# 常用键位（kVK_ANSI_*）
VK = {
    "s": 0x01, "m": 0x2E, "t": 0x11, "w": 0x0D, "h": 0x04,
    "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15,
}


class EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


class EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)


class GlobalHotkeys:
    def __init__(self, log=print):
        self.log = log
        self._actions = {}
        self._refs = []
        self._installed = False
        self._callback = None

    def register(self, key, modifiers, name, action):
        """key 可以是 VK 里的字符，或直接的键码。"""
        vk = VK.get(key, key) if isinstance(key, str) else key
        hot_id = EventHotKeyID(0x7768616C, len(self._actions) + 1)  # "whal"
        ref = ctypes.c_void_p()
        status = carbon.RegisterEventHotKey(
            ctypes.c_uint32(vk), ctypes.c_uint32(modifiers),
            hot_id, carbon.GetApplicationEventTarget(), 0, ctypes.byref(ref))
        if status != 0:
            self.log(f"快捷键 {name} 注册失败 status={status}")
            return False
        self._actions[hot_id.id] = action
        self._refs.append(ref)
        self.log(f"快捷键 {name} 已注册")
        return True

    def install(self):
        if self._installed:
            return

        def _handler(_call_ref, event, _user):
            try:
                hot_id = EventHotKeyID()
                size = ctypes.c_ulong(ctypes.sizeof(hot_id))
                carbon.GetEventParameter(
                    event, ctypes.c_uint32(kEventParamDirectObject),
                    ctypes.c_uint32(typeEventHotKeyID), None,
                    ctypes.c_ulong(ctypes.sizeof(hot_id)), None, ctypes.byref(hot_id))
                action = self._actions.get(hot_id.id)
                if action:
                    from PyObjCTools import AppHelper
                    AppHelper.callAfter(action)
            except Exception as exc:  # noqa: BLE001
                self.log(f"快捷键回调出错 {exc}")
            return 0

        self._callback = HANDLER(_handler)
        spec = EventTypeSpec(kEventClassKeyboard, kEventHotKeyPressed)
        carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
        target = carbon.GetApplicationEventTarget()
        st = carbon.InstallEventHandler(
            ctypes.c_void_p(target), self._callback, ctypes.c_uint32(1),
            ctypes.byref(spec), None, None)
        if st != 0:
            self.log(f"安装事件处理器失败 status={st}")
        self._installed = True
        self.log("全局快捷键已启用")
