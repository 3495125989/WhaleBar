#!/usr/bin/env python3
"""
Touch Bar 小鲸鱼 v2 —— 天气 + 输入框 + 鲸鱼娘

用 PyObjC 调用 AppKit，不需要编译。
系统常驻条走 NSTouchBar.presentSystemModalTouchBar:systemTrayItemIdentifier:
（PyObjC 没导出这个选择器，用 ctypes 直接发 ObjC 消息）。
"""

import ctypes
import os
import sys
import threading
import time

import config

import objc
from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory,
    NSTouchBar, NSCustomTouchBarItem, NSTextField, NSButton, NSImage,
    NSColor, NSFont, NSBezelStyleRounded, NSImageScaleProportionallyUpOrDown,
    NSTextAlignmentCenter, NSTextAlignmentLeft, NSApplicationActivationPolicyRegular,
    NSBezierPath,
    NSStatusBar, NSVariableStatusItemLength, NSMenu, NSMenuItem,
    NSWindow, NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSBackingStoreBuffered, NSFloatingWindowLevel, NSView, NSURL,
)
from Foundation import NSObject, NSTimer, NSRunLoop, NSRunLoopCommonModes
from objc import python_method
from PyObjCTools import AppHelper

HERE = os.path.dirname(os.path.abspath(__file__))
ASSISTANT_DIR = os.path.dirname(os.path.abspath(__file__))
ASSET_DIR = os.path.expanduser("~/Documents/deepseek harness 插件/touchbar")

WMO = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴", 45: "雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨", 56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒", 80: "阵雨", 81: "强阵雨",
    82: "暴雨", 85: "阵雪", 86: "强阵雪", 95: "雷阵雨", 96: "雷阵雨伴冰雹",
    99: "强雷暴伴冰雹",
}
CITY_CN = {
    "Beijing": "北京", "Shanghai": "上海", "Tianjin": "天津", "Chongqing": "重庆",
    "Guangzhou": "广州", "Shenzhen": "深圳", "Hangzhou": "杭州", "Nanjing": "南京",
    "Chengdu": "成都", "Wuhan": "武汉", "Xi'an": "西安", "Suzhou": "苏州",
}


class KeyablePanel(NSWindow):
    """无边框窗口默认 canBecomeKeyWindow 返回 False，所以永远收不到键盘。
    这个子类把它打开。"""

    def canBecomeKeyWindow(self):
        return True

    def keyDown_(self, event):
        # ESC 关闭
        if event.keyCode() == 53:
            self.orderOut_(None)
        else:
            objc.super(KeyablePanel, self).keyDown_(event)


# ───────────────── CoreAudio：按名字切换默认输出设备 ─────────────────
import ctypes as _ct

_ca = _ct.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
_SCOPE_GLOBAL = 0x676C6F62
_ELEM_MAIN = 0
_SYS_OBJ = 1


def _fourcc(t):
    return int.from_bytes(t.encode(), "big")


class _Addr(_ct.Structure):
    _fields_ = [("sel", _ct.c_uint32), ("scope", _ct.c_uint32), ("elem", _ct.c_uint32)]


def _prop_size(obj, sel, scope=_SCOPE_GLOBAL):
    a = _Addr(sel, scope, _ELEM_MAIN)
    n = _ct.c_uint32(0)
    _ca.AudioObjectGetPropertyDataSize(_ct.c_uint32(obj), _ct.byref(a), 0, None, _ct.byref(n))
    return n.value


def _dev_name(dev):
    a = _Addr(_fourcc("lnam"), _SCOPE_GLOBAL, _ELEM_MAIN)
    n = _ct.c_uint32(8)
    ref = _ct.c_void_p()
    _ca.AudioObjectGetPropertyData(_ct.c_uint32(dev), _ct.byref(a), 0, None, _ct.byref(n), _ct.byref(ref))
    if not ref.value:
        return ""
    try:
        return str(objc.objc_object(c_void_p=ref.value))
    except Exception:  # noqa: BLE001
        return ""


def _out_devices():
    """[(id, name)] 只保留有输出流的设备"""
    count = _prop_size(_SYS_OBJ, _fourcc("dev#")) // 4
    buf = (_ct.c_uint32 * count)()
    a = _Addr(_fourcc("dev#"), _SCOPE_GLOBAL, _ELEM_MAIN)
    n = _ct.c_uint32(count * 4)
    _ca.AudioObjectGetPropertyData(_ct.c_uint32(_SYS_OBJ), _ct.byref(a), 0, None, _ct.byref(n), buf)
    out = []
    for d in list(buf):
        if _prop_size(d, _fourcc("stm#"), _fourcc("outp")) > 0:
            out.append((d, _dev_name(d)))
    return out


def _default_out():
    a = _Addr(_fourcc("dOut"), _SCOPE_GLOBAL, _ELEM_MAIN)
    n = _ct.c_uint32(4)
    v = _ct.c_uint32(0)
    _ca.AudioObjectGetPropertyData(_ct.c_uint32(_SYS_OBJ), _ct.byref(a), 0, None, _ct.byref(n), _ct.byref(v))
    return v.value


def _set_default_out_raw(dev):
    a = _Addr(_fourcc("dOut"), _SCOPE_GLOBAL, _ELEM_MAIN)
    v = _ct.c_uint32(dev)
    return _ca.AudioObjectSetPropertyData(_ct.c_uint32(_SYS_OBJ), _ct.byref(a), 0, None, 4, _ct.byref(v))


def _set_default_out(dev):
    """统一入口：默认**不切换**系统输出。

    以前开律动/字幕会自动切到「耳机监听」（多输出设备），关掉再切回来——
    经常切错，而且会打断正在听的东西。现在默认跳过，
    需要旧行为就把 config.json 里 audio.autoSwitchOutput 改成 true。
    """
    if not (config.CFG.get("audio") or {}).get("autoSwitchOutput", False):
        log(f"自动切输出已禁用，保持当前设备（本想切到：{_dev_name(dev)}）")
        return -1
    return _set_default_out_raw(dev)


def _find_out(kw):
    for dev, name in _out_devices():
        if kw in name:
            return dev, name
    return None, None


def _is_virtual(name):
    """BlackHole / 多输出设备这些都不是"真正的"扬声器。"""
    return ("BlackHole" in name) or ("监听" in name) or ("多输出" in name)


def _pick_real_output():
    """挑一个真正能出声的设备：优先上次用的，其次内置扬声器/耳机。"""
    cands = [(d, n) for d, n in _out_devices() if not _is_virtual(n)]
    if not cands:
        return None, None
    last = (config.CFG.get("audio") or {}).get("lastOutputName") or ""
    for d, n in cands:
        if last and n == last:
            return d, n
    for kw in ("扬声器", "耳机", "Speaker", "Headphones"):
        for d, n in cands:
            if kw in n:
                return d, n
    return cands[0]


def _remember_output(name):
    """把"上次用的真实输出"记进 config.json，重启后也能切对。"""
    try:
        config.CFG.setdefault("audio", {})["lastOutputName"] = name
        config.save()
    except Exception as e:  # noqa: BLE001
        log(f"记住输出设备失败 {e}")


def _start_helper(ctrl):
    """保证 helper 子进程在跑（重复调用返回 True 不重复起）"""
    try:
        if getattr(ctrl, "_helper", None) is not None and ctrl._helper.poll() is None:
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        return ctrl._start_meter()
    except Exception as e:  # noqa: BLE001
        log(f"启动 helper 失败 {e}")
        return False


def subprocess_call(argv):
    import subprocess
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:  # noqa: BLE001
        log(f"命令失败 {e}")


class BarView(NSView):
    """一条连续的鲸鱼蓝实心条，左边起，长度 = level。"""

    level = 0.0

    def drawRect_(self, rect):
        b = self.bounds()
        w = b.size.width * float(getattr(self, "level", 0.0))
        if w < 3:
            return
        r = ((0.0, 0.0), (w, b.size.height))
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(r, 7.0, 7.0)
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.47, 0.58, 0.88, 1.0).set()
        path.fill()


class BarContent(NSView):
    """满宽容器：按自己实际的宽度重新排版，适配不同尺寸的 Touch Bar。

    所有子控件都在 layout() 里用相对位置摆放，所以整条多宽都不会把
    右边的鲸鱼/收起按钮挤出屏幕。
    """

    _logged = False

    def layout(self):
        b = self.bounds()
        W, H = b.size.width, b.size.height
        if not BarContent._logged:
            BarContent._logged = True
            log(f"触摸条容器实际尺寸 = {W:.1f} x {H:.1f} pt")

        pad = 6.0
        # 右侧固定：鲸鱼 + 收
        quit_w, whale_w = 44.0, 32.0
        right = W - pad
        self.quit_btn.setFrame_(((right - quit_w, 2.0), (quit_w, H - 4)))
        right -= quit_w
        self.whale_btn.setFrame_(((right - whale_w, 1.0), (whale_w, H - 2)))
        right -= whale_w

        # 左侧天气：按比例给，但有上下限
        weather_w = max(72.0, min(120.0, W * 0.15))
        self.weather_btn.setFrame_(((pad, 2.0), (weather_w, H - 4)))

        # 中间：律动条 / 字幕，占剩下的全部
        mid_x = pad + weather_w + 8.0
        mid_w = max(40.0, right - mid_x - 8.0)
        self.level_view.setFrame_(((mid_x, 7.0), (mid_w, 16.0)))
        self.subtitle_label.setFrame_(((mid_x, 3.0), (mid_w, 24.0)))


def log(m):
    print(f"[whale] {m}", flush=True)


class Controller(NSObject):
    def init(self):
        self = objc.super(Controller, self).init()
        if self is None:
            return None
        self.items = {}
        self.weather_label = None
        self.text_field = None
        return self

    # NSTouchBarDelegate：触摸条向我们要控件
    def touchBar_makeItemForIdentifier_(self, touchBar, identifier):
        item = self.items.get(str(identifier))
        log(f"makeItem {identifier} -> {type(item).__name__ if item else None}")
        return item

    @python_method
    def _ensure_hidden_window(self):
        if getattr(self, "input_window", None) is not None:
            return
        w = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (6, 26)), 0, NSBackingStoreBuffered, False)
        w.setOpaque_(False)
        w.setAlphaValue_(0.0)
        w.setIgnoresMouseEvents_(True)
        w.setLevel_(NSFloatingWindowLevel)
        f = NSTextField.alloc().initWithFrame_(((0, 0), (6, 26)))
        f.setDelegate_(self)
        f.setTarget_(self)
        f.setAction_("sendText:")
        w.setContentView_(f)
        self.input_window = w
        self.hidden_field = f

    def startInput_(self, sender):
        """点胶囊：把键盘焦点悄悄拿到我们这边，字同步显示在触摸条上。"""
        self._ensure_hidden_window()
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.input_window.makeKeyAndOrderFront_(None)
        self.input_window.makeFirstResponder_(self.hidden_field)
        self._sync_display()
        log("进入输入模式")

    def controlTextDidChange_(self, note):
        self._sync_display()

    @python_method
    def _sync_display(self):
        if getattr(self, "input_button", None) is None:
            return
        src = getattr(self, "panel_field", None) or getattr(self, "hidden_field", None)
        txt = str(src.stringValue()) if src is not None else ""
        if len(txt) > 16:
            txt = txt[:16] + "…"
        self.input_button.setTitle_(f"  {txt}" if txt else "  跟我说点什么…")

    def showInputPanel_(self, sender):
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if getattr(self, "panel", None) is None:
            rect = ((0, 0), (460, 60))
            panel = KeyablePanel.alloc().initWithContentRect_styleMask_backing_defer_(
                rect, 0, NSBackingStoreBuffered, False,
            )
            panel.setOpaque_(False)
            panel.setBackgroundColor_(NSColor.clearColor())
            try:
                panel.setHasShadow_(True)
            except Exception:  # noqa: BLE001
                pass
            panel.setLevel_(NSFloatingWindowLevel)
            holder = objc.lookUpClass("NSView").alloc().initWithFrame_(((0, 0), (460, 60)))
            holder.setWantsLayer_(True)
            if holder.layer() is not None:
                holder.layer().setBackgroundColor_(
                    NSColor.colorWithCalibratedWhite_alpha_(0.12, 0.96).CGColor())
                holder.layer().setCornerRadius_(14.0)
            f = NSTextField.alloc().initWithFrame_(((18, 17), (424, 26)))
            f.setFont_(NSFont.systemFontOfSize_(14))
            f.setPlaceholderString_("说完按回车")
            f.setTarget_(self)
            f.setAction_("sendText:")
            f.setDelegate_(self)
            holder.addSubview_(f)
            panel.setContentView_(holder)
            panel.setReleasedWhenClosed_(False)
            panel.setLevel_(NSFloatingWindowLevel)
            panel.setDelegate_(self)
            self.panel = panel
            self.panel_field = f
        self.panel.center()
        self.panel.makeKeyAndOrderFront_(None)
        self.panel.makeFirstResponder_(self.panel_field)
        log("弹出输入窗")

    def windowDidResignKey_(self, note):
        w = getattr(self, "panel", None)
        if w is not None and note.object() is w:
            w.orderOut_(None)
            log("面板失焦，收起")

    def toggleBar_(self, sender):
        self.bar_visible = not getattr(self, "bar_visible", False)
        if self.bar_visible:
            self._present()
            if getattr(self, "status_menu_item", None) is not None:
                self.status_menu_item.setTitle_("隐藏 Touch Bar")
        else:
            self._dismiss()
            if getattr(self, "status_menu_item", None) is not None:
                self.status_menu_item.setTitle_("显示 Touch Bar")
        log(f"触摸条可见 = {self.bar_visible}")

    @python_method
    def _present(self):
        try:
            present_system_modal(self.touch_bar)
            log("已显示触摸条")
        except Exception as e:  # noqa: BLE001
            log(f"显示失败 {e}")

    @python_method
    def _dismiss(self):
        try:
            dismiss_system_modal(self.touch_bar)
            log("已隐藏触摸条")
        except Exception as e:  # noqa: BLE001
            log(f"隐藏失败 {e}")

    @python_method
    def _start_meter(self):
        import subprocess
        import threading as _th

        # 互斥：律动和字幕同时只开一个
        if getattr(self, "_sub_on", False):
            log("开律动 → 先关掉字幕")
            try:
                self.toggleSubtitle_(None)
            except Exception as e:  # noqa: BLE001
                log(f"关字幕失败 {e}")

        # 新方案：SCK 抓系统声音（不装 BlackHole、不切输出）
        app = os.path.join(ASSISTANT_DIR, "律动助手.app")
        if os.path.isdir(app):
            subprocess_call(["/usr/bin/pkill", "-f", "sck_meter.py"])
            subprocess_call(["/usr/bin/pkill", "-f", "meter_helper.py"])
            subprocess_call(["/usr/bin/open", "-n", app])
            self._helper = None
            log("律动：已启动 律动助手.app（SCK 抓音）")
        else:
            log("⚠️ 没找到 律动助手.app，先跑 ./build.command")
            return False
        self._meter_on = True

        def reader():
            import time as _t
            cells = 48
            n = 0
            while getattr(self, "_meter_on", False):
                try:
                    with open("/tmp/whale_level") as f:
                        v = float(f.read().strip() or 0)
                except Exception:  # noqa: BLE001
                    v = 0.0
                self._level_now = min(1.0, max(0.0, v))
                bar = f"{self._level_now:.2f}"
                try:
                    AppHelper.callAfter(self._apply_bar, bar)
                except Exception as e:  # noqa: BLE001
                    if n % 40 == 0:
                        log(f"更新失败 {e}")
                n += 1
                if n % 100 == 0:
                    log(f"level={v:.3f} bar={bar}")
                _t.sleep(0.06)

        _th.Thread(target=reader, daemon=True).start()
        log("音量采集已启动（helper 子进程）")
        return True

    @python_method
    def _apply_bar(self, bar):
        self._am = getattr(self, "_am", 0) + 1
        if self._am <= 3 or self._am % 60 == 0:
            log(f"APPLY #{self._am} 线程={__import__('threading').current_thread().name}")
        lb = getattr(self, "level_label", None)
        if lb is not None:
            lb.level = float(getattr(self, "_level_now", 0.0))
            lb.setNeedsDisplay_(True)
            self._ab = getattr(self, "_ab", 0) + 1
            if self._ab % 40 == 0:
                log(f"redraw #{self._ab}")
            self._ab = getattr(self, "_ab", 0) + 1
            if self._ab % 25 == 0:
                log(f"bar 已刷新 #{self._ab} 当前={bar}")

    def _refresh_lang_checks(self):
        """把当前选中的语言项打上对勾。"""
        cur_rec = (config.CFG.get("subtitle") or {}).get("language", "zh-CN")
        tr = config.CFG.get("translate") or {}
        cur_tr = "none" if (tr.get("provider") or "none") == "none" else tr.get("to", "zh")
        items = getattr(self, "_lang_items", None) or []
        log(f"刷新对勾：共 {len(items)} 项，当前 rec={cur_rec} tr={cur_tr}")
        for item, spec in items:
            kind, value = str(spec).split(":", 1)
            on = (kind == "rec" and value == cur_rec) or (kind == "tr" and value == cur_tr)
            try:
                item.setState_(1 if on else 0)
            except Exception:  # noqa: BLE001
                pass

    def pickLang_(self, sender):
        """菜单里选了识别语言：写进 config.json，字幕助手在跑就重启它。"""
        spec = sender.representedObject()   # 形如 "rec:en-US" 或 "tr:zh"
        if not spec:
            return
        kind, value = str(spec).split(":", 1)
        config.reload()   # 防止用启动时的旧副本覆盖别的设置
        if kind == "rec":
            config.CFG.setdefault("subtitle", {})["language"] = value
        elif kind == "tr":
            tr = config.CFG.setdefault("translate", {})
            if value == "none":
                tr["provider"] = "none"
            else:
                tr["provider"] = "baidu" if tr.get("appid") else "none"
                tr["to"] = value
            if value != "none" and not tr.get("appid"):
                log("⚠️ 还没填百度翻译 appid/key，先只显示原文")
        config.save()
        log(f"语言设置已保存：{kind}={value}")
        # 字幕助手会自己盯 config.json，不用重启
        self._refresh_lang_checks()
        self._refresh_tr_enabled()
        log(f"已保存 {spec}（字幕助手 2 秒内自动生效）")

    def switchToMonitor_(self, sender):
        """手动：切到多输出设备（含 BlackHole），律动/字幕才有声音。"""
        dev, name = _find_out("耳机监听")
        if dev is None:
            dev, name = _find_out("BlackHole")
        if dev is None:
            log("没找到含 BlackHole 的输出设备，先去装 BlackHole / 建多输出设备")
            return
        _set_default_out_raw(dev)
        log(f"手动切到：{name}")

    def switchToSpeaker_(self, sender):
        """手动：切回真正的扬声器/耳机。"""
        dev, name = _pick_real_output()
        if dev is None:
            log("没找到可用的真实输出设备")
            return
        _set_default_out_raw(dev)
        log(f"手动切回：{name}")

    def toggleMeter_(self, sender):
        log(f"toggleMeter 当前={getattr(self, '_meter_on', None)}")
        if getattr(self, "_meter_on", False):
            # ── 关：切回真正的耳机/扬声器，不杀 helper，让柱子自然回落 ──
            self._meter_on = False
            target = getattr(self, "_prev_output", None)
            tname = _dev_name(target) if target else ""
            alive = any(d == target for d, _ in _out_devices()) if target else False
            if (not alive) or _is_virtual(tname):
                # 1) 先按"上次记住的名字"精确找
                last = (config.CFG.get("audio") or {}).get("lastOutputName") or ""
                d2, n2 = (None, None)
                if last:
                    d2, n2 = _find_out(last)
                # 2) 找不到就挑一个真的能出声的
                if d2 is None:
                    d2, n2 = _pick_real_output()
                if d2 is not None:
                    target, tname = d2, n2
                log(f"切回兜底设备：{tname or '无'}")
            try:
                if target:
                    _set_default_out(target)
                    log(f"输出已切回：{tname}")
            except Exception as e:  # noqa: BLE001
                log(f"切回输出失败 {e}")
            self._prev_output = None
            try:
                if getattr(self, "meter_menu_item", None) is not None:
                    self.meter_menu_item.setTitle_("打开音量律动")
            except Exception as e:  # noqa: BLE001
                log(f"改菜单标题失败 {e}")
            try:
                if getattr(self, "level_label", None) is not None:
                    self.level_label.setHidden_(True)
            except Exception:  # noqa: BLE001
                pass
            try:
                if getattr(self, "_helper", None) is not None:
                    self._helper.terminate()
                    self._helper = None
                    log("采集进程已退出（旧 helper）")
                subprocess_call(["/usr/bin/pkill", "-f", "sck_meter.py"])
                log("律动助手已退出")
            except Exception as e:  # noqa: BLE001
                log(f"停采集失败 {e}")
            log("律动已关闭（柱子已隐藏）")
            return

        # ── 开：记住当前真实输出，切到含 BlackHole 的多输出 ──
        cur = _default_out()
        cname = _dev_name(cur)
        if not _is_virtual(cname):
            self._prev_output = cur
            _remember_output(cname)
            log(f"记住原输出：{cname}")
        dev, name = _find_out("耳机监听")
        if dev is None:
            dev, name = _find_out("BlackHole")
        if dev is not None:
            _set_default_out(dev)
            log(f"输出已切到：{name}")
        else:
            log("没找到含 BlackHole 的输出设备")
        if _start_helper(self):
            self._meter_on = True
            try:
                if getattr(self, "level_label", None) is not None:
                    self.level_label.setHidden_(False)
            except Exception:  # noqa: BLE001
                pass
            try:
                if getattr(self, "meter_menu_item", None) is not None:
                    self.meter_menu_item.setTitle_("关闭音量律动")
            except Exception as e:  # noqa: BLE001
                log(f"改菜单标题失败 {e}")

    def toggleSubtitle_(self, sender):
        if getattr(self, "_sub_on", False):
            subprocess_call(["/usr/bin/pkill", "-f", "sck_subtitle.py"])
            subprocess_call(["/usr/bin/pkill", "-f", "subtitle_stream.py"])
            subprocess_call(["/usr/bin/osascript", "-e",
                             'tell application "Terminal" to close (every window whose name contains "subtitle_stream")'])
            try:
                prev = getattr(self, "_sub_prev_out", None)
                if prev:
                    _set_default_out(prev)
                    log(f"字幕：输出已切回 {_dev_name(prev)}")
                    self._sub_prev_out = None
            except Exception as e:  # noqa: BLE001
                log(f"字幕切回失败 {e}")
            self._sub_on = False
            self.subtitle_label.setHidden_(True)
            self.level_label.setHidden_(False)
            try:
                self.subtitle_menu_item.setTitle_("开启字幕")
            except Exception:  # noqa: BLE001
                pass
            log("字幕已关闭")
            return
        # 互斥：律动和字幕同时只开一个
        if getattr(self, "_meter_on", False):
            log("开字幕 → 先关掉律动")
            try:
                self.toggleMeter_(None)
            except Exception as e:  # noqa: BLE001
                log(f"关律动失败 {e}")

        # 新方案：ScreenCaptureKit 直接抓系统声音，不碰 BlackHole、不动系统输出
        subprocess_call(["/usr/bin/pkill", "-f", "sck_subtitle.py"])
        subprocess_call(["/usr/bin/pkill", "-f", "subtitle_stream.py"])
        app = os.path.join(ASSISTANT_DIR, "字幕助手.app")
        if os.path.isdir(app):
            subprocess_call(["/usr/bin/open", "-n", app])
            log("字幕：已启动 字幕助手.app（SCK 抓音，不切输出）")
        else:
            log("⚠️ 没找到 字幕助手.app，先跑 ./build.command")
        self._sub_on = True
        self.level_label.setHidden_(True)
        self.subtitle_label.setHidden_(False)
        try:
            self.subtitle_menu_item.setTitle_("关闭字幕")
        except Exception:  # noqa: BLE001
            pass
        import threading as _th
        _th.Thread(target=self._subtitle_reader, daemon=True).start()
        log("字幕已开启（流式）")

    @python_method
    def _subtitle_reader(self):
        """常驻：只要 /tmp/whale_text 在 3 秒内更新过，就把它显示出来。
        这样不管字幕是菜单开的还是 .command 开的，都能显示。"""
        import time as _t

        while True:
            fresh = False
            t = ""
            try:
                if _t.time() - os.path.getmtime("/tmp/whale_text") < 8.0:
                    with open("/tmp/whale_text") as f:
                        t = f.read().strip()
                    fresh = bool(t)
            except Exception:  # noqa: BLE001
                fresh = False
            AppHelper.callAfter(self._apply_text, t if fresh else "")
            _t.sleep(0.15)

    @python_method
    def _apply_text(self, t):
        lb = getattr(self, "subtitle_label", None)
        bar = getattr(self, "level_label", None)
        if lb is None:
            return
        if t:
            lb.setStringValue_(str(t)[-30:])
            lb.setHidden_(False)
            lb.setNeedsDisplay_(True)
            if bar is not None:
                bar.setHidden_(True)
        else:
            if not lb.isHidden():
                lb.setHidden_(True)
                if bar is not None and getattr(self, "_meter_on", False):
                    bar.setHidden_(False)

    def _helper_running(self, pattern):
        import subprocess
        try:
            return subprocess.run(["/usr/bin/pgrep", "-f", pattern],
                                  capture_output=True).returncode == 0
        except Exception:  # noqa: BLE001
            return True

    def healthCheck_(self, timer):
        """助手挂了就自动拉起来——比让用户看着空屏猜强。"""
        now = time.time()
        if getattr(self, "_sub_on", False) and not self._helper_running("sck_subtitle.py"):
            if now - getattr(self, "_sub_heal", 0) > 15:
                self._sub_heal = now
                log("⚠️ 字幕助手不在了，自动重启")
                subprocess_call(["/usr/bin/open", "-n",
                                 os.path.join(ASSISTANT_DIR, "字幕助手.app")])
        if getattr(self, "_meter_on", False) and not self._helper_running("sck_meter.py"):
            if now - getattr(self, "_meter_heal", 0) > 15:
                self._meter_heal = now
                log("⚠️ 律动助手不在了，自动重启")
                subprocess_call(["/usr/bin/open", "-n",
                                 os.path.join(ASSISTANT_DIR, "律动助手.app")])

    # ── 翻译 key 设置 ──
    def _refresh_tr_enabled(self):
        """没填 key 时，除了「不翻译」以外全禁用。"""
        tr = config.CFG.get("translate") or {}
        ready = bool(tr.get("appid")) and bool(tr.get("key")) and (tr.get("provider") or "none") != "none"
        for item, spec in (getattr(self, "_lang_items", None) or []):
            kind, value = str(spec).split(":", 1)
            if kind != "tr":
                continue
            try:
                item.setEnabled_(True if (value == "none" or ready) else False)
            except Exception:  # noqa: BLE001
                pass

    def setupTranslateKey_(self, sender):
        """弹框让用户填 APPID / 密钥。填了就能翻译，不填就只做识别。"""
        from AppKit import NSAlert, NSTextField, NSView, NSMakeRect
        alert = NSAlert.alloc().init()
        alert.setMessageText_("翻译 API（百度）")
        alert.setInformativeText_(
            "填 APPID 和密钥即可开启翻译；留空则只能识别字幕。\n"
            "申请地址：fanyi-api.baidu.com（免费，每月 5 万字）")

        box = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 340, 60))
        tr = config.CFG.get("translate") or {}

        lab1 = NSTextField.labelWithString_("APPID")
        lab1.setFrame_(NSMakeRect(0, 36, 60, 20))
        box.addSubview_(lab1)
        f1 = NSTextField.alloc().initWithFrame_(NSMakeRect(64, 34, 270, 24))
        f1.setStringValue_(str(tr.get("appid") or ""))
        box.addSubview_(f1)

        lab2 = NSTextField.labelWithString_("密钥")
        lab2.setFrame_(NSMakeRect(0, 6, 60, 20))
        box.addSubview_(lab2)
        f2 = NSTextField.alloc().initWithFrame_(NSMakeRect(64, 4, 270, 24))
        f2.setStringValue_(str(tr.get("key") or ""))
        box.addSubview_(f2)

        alert.setAccessoryView_(box)
        alert.addButtonWithTitle_("保存")
        alert.addButtonWithTitle_("清空")

        resp = alert.runModal()
        config.reload()
        tr = config.CFG.setdefault("translate", {})
        if resp == 1001:          # 清空
            tr["appid"] = ""
            tr["key"] = ""
            tr["provider"] = "none"
            config.save()
            log("翻译 key 已清空 → 只做识别")
        else:
            appid = str(f1.stringValue()).strip()
            key = str(f2.stringValue()).strip()
            tr["appid"] = appid
            tr["key"] = key
            tr["provider"] = "baidu" if (appid and key) else "none"
            config.save()
            log(f"翻译 key 已保存（{'已启用' if appid and key else '未填全，翻译关闭'}）")
        self._refresh_lang_checks()
        self._refresh_tr_enabled()

    # ── 鲸鱼点开哪个 app ──
    def pickApp_(self, sender):
        spec = sender.representedObject()
        if spec == "custom":
            from AppKit import NSAlert, NSTextField, NSView, NSMakeRect
            alert = NSAlert.alloc().init()
            alert.setMessageText_("鲸鱼图标打开哪个应用？")
            alert.setInformativeText_("填 macOS 应用名（不含 .app），比如 Safari、Visual Studio Code")
            box = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 300, 26))
            fld = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 300, 24))
            fld.setStringValue_(str(config.CFG.get("appName") or ""))
            box.addSubview_(fld)
            alert.setAccessoryView_(box)
            alert.addButtonWithTitle_("保存")
            alert.addButtonWithTitle_("取消")
            if alert.runModal() == 1000:
                name = str(fld.stringValue()).strip()
                if name:
                    config.reload()
                    config.CFG["appName"] = name
                    config.save()
                    log(f"鲸鱼将打开：{name}")
        elif spec:
            config.reload()
            config.CFG["appName"] = str(spec)
            config.save()
            log(f"鲸鱼将打开：{spec}")

    def tickMeter_(self, timer):
        self._t = getattr(self, "_t", 0) + 1
        if self._t % 20 == 0:
            log(f"tick #{self._t} meter_on={getattr(self,'_meter_on',None)} label={'有' if getattr(self,'level_label',None) else '无'}")
        if not getattr(self, "_meter_on", False):
            return
        if getattr(self, "level_label", None) is None:
            return
        raw = min(1.0, getattr(self, "_level", 0.0) * 6.0)
        disp = max(raw, getattr(self, "_disp", 0.0) * 0.82)   # 快起慢落
        self._disp = disp
        cells = 12
        n = int(disp * cells)
        bar = "▮" * n + "▯" * (cells - n)
        self.level_label.setTitle_(bar)
        self._mt = getattr(self, "_mt", 0) + 1
        if self._mt % 16 == 0:
            log(f"meter lvl={getattr(self,'_level',0):.4f} disp={disp:.3f} bar={bar}")

    def sendClipboard_(self, sender):
        from Foundation import NSPasteboard
        txt = str(NSPasteboard.generalPasteboard().stringForType_("public.utf8-plain-text") or "")
        if not txt.strip():
            log("剪贴板是空的")
            return
        log(f"发送剪贴板：{txt[:40]}")
        self._deliver(txt)

    def openWeather_(self, sender):
        from AppKit import NSWorkspace
        log("点天气：打开系统「天气」")
        ok = NSWorkspace.sharedWorkspace().launchApplication_("Weather")
        log(f"launch Weather -> {ok}")

    def openDSH_(self, sender):
        from AppKit import NSWorkspace
        log("点鲸鱼：打开 DeepSeek Harness")
        ws = NSWorkspace.sharedWorkspace()
        ok = ws.launchApplication_(config.CFG["appName"])
        log(f"launchApplication -> {ok}")

    def quitApp_(self, sender):
        log("点了关闭，退出（顺手清理 helper）")
        for pat in ("meter_helper.py", "subtitle_stream.py"):
            subprocess_call(["/usr/bin/pkill", "-f", pat])
        NSApplication.sharedApplication().terminate_(None)

    def sendText_(self, sender):
        text = str(sender.stringValue()).strip()
        if not text:
            return
        log(f"回车：{text}")
        self._deliver(text)
        sender.setStringValue_("")
        if getattr(self, "input_button", None) is not None:
            self.input_button.setTitle_("  跟我说点什么…")
        if getattr(self, "panel_field", None) is not None:
            self.panel_field.setStringValue_("")
        if getattr(self, "input_window", None) is not None:
            self.input_window.orderOut_(None)
        from AppKit import NSWorkspace
        NSWorkspace.sharedWorkspace().launchApplication_(config.CFG["appName"])

    @python_method
    def _deliver(self, text):
        from Foundation import NSPasteboard
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(text, "public.utf8-plain-text")
        log("文字已放进剪贴板")

        # 先试 AppleScript（需要「自动化」权限）
        script = (
            'tell application "System Events"\n'
            '  set frontmost of (first process whose name contains "DeepSeek") to true\n'
            '  delay 0.25\n'
            '  keystroke "v" using command down\n'
            '  delay 0.1\n'
            '  key code 36\n'
            'end tell'
        )
        ok = False
        try:
            from Foundation import NSAppleScript
            sa = NSAppleScript.alloc().initWithSource_(script)
            res, err = sa.executeAndReturnError_(None)
            ok = err is None
            log(f"AppleScript 发送 ok={ok} err={err}")
        except Exception as e:  # noqa: BLE001
            log(f"AppleScript 异常 {e}")

        if ok:
            return

        # 退路：直接合成 Cmd+V（需要「辅助功能」权限）
        log("改走 CGEvent 合成按键")
        try:
            from AppKit import NSWorkspace
            NSWorkspace.sharedWorkspace().launchApplication_(config.CFG["appName"])
            import time
            time.sleep(0.4)
            from Quartz import (
                CGEventCreateKeyboardEvent, CGEventPost, CGEventSetFlags,
                kCGHIDEventTap, kCGEventFlagMaskCommand,
            )
            for code in (9, 9, 36):  # v 按下、v 抬起、回车
                down = code != 9 or True
                ev = CGEventCreateKeyboardEvent(None, code, True)
                if code == 9:
                    CGEventSetFlags(ev, kCGEventFlagMaskCommand)
                CGEventPost(kCGHIDEventTap, ev)
                ev2 = CGEventCreateKeyboardEvent(None, code, False)
                if code == 9:
                    CGEventSetFlags(ev2, kCGEventFlagMaskCommand)
                CGEventPost(kCGHIDEventTap, ev2)
            log("CGEvent 已发出")
        except Exception as e:  # noqa: BLE001
            log(f"CGEvent 失败 {e}")

    def refreshWeather_(self, timer):
        threading.Thread(target=self._fetch, daemon=True).start()

    @python_method
    def _fetch(self):
        import json
        import urllib.request

        def get(u):
            req = urllib.request.Request(u, headers={"User-Agent": "curl/8"})
            with urllib.request.urlopen(req, timeout=8) as r:
                return json.load(r)

        geos = [
            lambda: get("https://ipinfo.io/json"),
            lambda: get("https://ifconfig.co/json"),
        ]
        text = "天气失败"
        for g in geos:
            try:
                geo = g()
                lat = float(geo.get("latitude") or geo["loc"].split(",")[0])
                lon = float(geo.get("longitude") or geo["loc"].split(",")[1])
                raw_city = geo.get("city", "")
                city = CITY_CN.get(raw_city, raw_city or "?")
                cur = get(
                    "https://api.open-meteo.com/v1/forecast"
                    f"?latitude={lat:.4f}&longitude={lon:.4f}"
                    "&current=temperature_2m,weather_code&timezone=auto"
                )["current"]
                text = f"{city} {round(cur['temperature_2m'])}° {WMO.get(cur['weather_code'], '?')}"
                break
            except Exception as e:  # noqa: BLE001
                log(f"天气源失败 {e}")
        AppHelper.callAfter(self._set_weather, text)

    @python_method
    def _set_weather(self, text):
        log(f"天气：{text}")
        if self.weather_label is not None:
            self.weather_label.setTitle_(text)
        if text in ("天气失败", "天气 …"):
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                60.0, self, "refreshWeather:", None, False
            )
            log("天气失败，60 秒后重试")


def bar_width_for_model():
    """按机型推触摸条可用宽度。

    带 Touch Bar 的 MacBook Pro（13"/15"/16"）条都是 2170×60 像素，
    也就是 **1085×30 点**；右边要给系统托盘留位置。
    不同机型目前实测一致，但保留按型号判断的位置，将来出新型号好加。
    """
    try:
        import subprocess
        model = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.model"],
                               capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:  # noqa: BLE001
        model = ""

    BAR_PT = 1085          # 触摸条总宽度（点）
    RESERVE = 105          # 右边留给系统/托盘
    WIDTHS = {
        "MacBookPro13": (BAR_PT, RESERVE),
        "MacBookPro14": (BAR_PT, RESERVE),
        "MacBookPro15": (BAR_PT, RESERVE),
        "MacBookPro16": (BAR_PT, RESERVE),
    }
    for prefix, (total, reserve) in WIDTHS.items():
        if model.startswith(prefix):
            cfg = config.CFG.get("bar") or {}
            if cfg.get("width"):
                return float(cfg["width"])     # 配置优先
            return float(total - reserve)
    cfg = config.CFG.get("bar") or {}
    return float(cfg.get("width") or 980)


def build_bar(ctrl):
    """整条触摸条用一个满宽自绘容器，自己排版，避免全挤在左边。"""
    bar = NSTouchBar.alloc().init()
    bar.setDelegate_(ctrl)

    # 宽度从配置来；容器内部按实际宽度排版，右边两个按钮永远贴右边缘
    W = bar_width_for_model()
    log(f"检测机型宽度 → {W:.0f}pt")
    H = 30
    item = NSCustomTouchBarItem.alloc().initWithIdentifier_("main")
    container = BarContent.alloc().initWithFrame_(((0, 0), (W, H)))

    # ── 天气（可点，打开系统天气 App）──
    w = NSButton.alloc().initWithFrame_(((6, 2), (110, 26)))
    w.setBordered_(False)
    w.setTitle_("定位中…")
    w.setFont_(NSFont.systemFontOfSize_(13))
    w.setAlignment_(NSTextAlignmentCenter)
    w.setTarget_(ctrl)
    w.setAction_("openWeather:")
    container.addSubview_(w)
    ctrl.weather_label = w
    container.weather_btn = w

    # ── 音量律动 ──
    st = NSTextField.labelWithString_("")
    st.setFrame_(((118, 3), (482, 24)))
    st.setFont_(NSFont.systemFontOfSize_(16))
    st.setTextColor_(NSColor.whiteColor())
    st.setHidden_(True)
    ctrl.subtitle_label = st
    container.addSubview_(st)
    container.subtitle_label = st

    lv = BarView.alloc().initWithFrame_(((118, 7), (482, 16)))
    lv.level = 0.0
    ctrl.level_label = lv
    container.addSubview_(lv)
    container.level_view = lv

    # ── 鲸鱼（点开 DSH）──
    img = NSImage.alloc().initWithContentsOfFile_(
        os.path.join(ASSET_DIR, "whale-icon-60.png"))
    wb = NSButton.alloc().initWithFrame_(((612, 1), (32, 28)))
    if img is not None:
        img.setSize_((28, 28))
        wb.setImage_(img)
        wb.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        log("鲸鱼图标已加载")
    else:
        wb.setTitle_("鲸")
    wb.setBordered_(False)
    wb.setTitle_("")
    wb.setTarget_(ctrl)
    wb.setAction_("openDSH:")
    container.addSubview_(wb)
    container.whale_btn = wb

    # ── 收 ──
    q = NSButton.alloc().initWithFrame_(((654, 2), (44, 26)))
    q.setBordered_(False)
    q.setTitle_("收")
    q.setFont_(NSFont.systemFontOfSize_(13))
    q.setTarget_(ctrl)
    q.setAction_("toggleBar:")
    container.addSubview_(q)
    container.quit_btn = q

    container.layout()

    item.setView_(container)
    ctrl.items = {"main": item}
    ctrl.main_container = container
    bar.setDefaultItemIdentifiers_(["main"])
    return bar


def present_system_modal(bar, tray_identifier=b"com.yoshiro.whale.tray"):
    """ctypes 直接发 presentSystemModalTouchBar:systemTrayItemIdentifier:
    标识符必须是真的 NSString，用 CoreFoundation 造一个（PyObjC 会把 NSString 转成 str）。"""
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    ident = cf.CFStringCreateWithCString(None, tray_identifier, 0x08000100)  # UTF-8

    libobjc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    libobjc.objc_getClass.restype = ctypes.c_void_p
    libobjc.objc_getClass.argtypes = [ctypes.c_char_p]
    libobjc.sel_registerName.restype = ctypes.c_void_p
    libobjc.sel_registerName.argtypes = [ctypes.c_char_p]

    cls = libobjc.objc_getClass(b"NSTouchBar")
    sel = libobjc.sel_registerName(b"presentSystemModalTouchBar:systemTrayItemIdentifier:")
    msgsend = libobjc.objc_msgSend
    msgsend.restype = ctypes.c_void_p
    msgsend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]

    msgsend(cls, sel, ctypes.c_void_p(objc.pyobjc_id(bar)), ctypes.c_void_p(ident))
    return True


def _objc_msg(class_name, selector, *args):
    """给某个 ObjC 类发类消息，参数都是 ctypes.c_void_p。"""
    libobjc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    libobjc.objc_getClass.restype = ctypes.c_void_p
    libobjc.objc_getClass.argtypes = [ctypes.c_char_p]
    libobjc.sel_registerName.restype = ctypes.c_void_p
    libobjc.sel_registerName.argtypes = [ctypes.c_char_p]
    libobjc.objc_msgSend.restype = ctypes.c_void_p
    libobjc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + \
        [ctypes.c_void_p] * len(args)
    cls = libobjc.objc_getClass(class_name)
    sel = libobjc.sel_registerName(selector)
    return libobjc.objc_msgSend(cls, sel, *args)


def dismiss_system_modal(bar):
    _objc_msg(b"NSTouchBar", b"dismissSystemModalTouchBar:",
              ctypes.c_void_p(objc.pyobjc_id(bar)))


def install_status_item(ctrl):
    """菜单栏常驻图标：显示/隐藏触摸条、打开 DSH、退出。"""
    sb = NSStatusBar.systemStatusBar()
    item = sb.statusItemWithLength_(NSVariableStatusItemLength)
    btn = item.button()
    img = NSImage.alloc().initWithContentsOfFile_(
        os.path.join(ASSET_DIR, "whale-icon-60.png"))
    if img is not None:
        img.setSize_((18, 18))
        btn.setImage_(img)
    else:
        btn.setTitle_("🐳")

    menu = NSMenu.alloc().init()

    mi_toggle = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "隐藏 Touch Bar", "toggleBar:", "")
    mi_toggle.setTarget_(ctrl)
    menu.addItem_(mi_toggle)

    mi_open = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "打开 DeepSeek Harness", "openDSH:", "")
    mi_open.setTarget_(ctrl)
    menu.addItem_(mi_open)

    mi_input = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "输入…", "showInputPanel:", "i")
    mi_input.setTarget_(ctrl)
    menu.addItem_(mi_input)

    mi_sub = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "开启字幕", "toggleSubtitle:", "s")
    mi_sub.setTarget_(ctrl)
    menu.addItem_(mi_sub)
    ctrl.subtitle_menu_item = mi_sub

    mi_meter = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "打开音量律动", "toggleMeter:", "m")
    mi_meter.setTarget_(ctrl)
    menu.addItem_(mi_meter)
    ctrl.meter_menu_item = mi_meter

    # ── 识别语言 ──
    rec_menu = NSMenu.alloc().initWithTitle_("识别语言")
    for title, code in (("中文", "zh-CN"), ("English", "en-US"),
                        ("日本語", "ja-JP"), ("한국어", "ko-KR")):
        it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, "pickLang:", "")
        it.setTarget_(ctrl)
        it.setRepresentedObject_(f"rec:{code}")
        rec_menu.addItem_(it)
        ctrl._lang_items = (getattr(ctrl, "_lang_items", None) or []) + [(it, f"rec:{code}")]
    mi_rec = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("识别语言", "", "")
    mi_rec.setSubmenu_(rec_menu)
    menu.addItem_(mi_rec)

    # ── 翻译成 ──
    tr_menu = NSMenu.alloc().initWithTitle_("翻译成")
    for title, code in (("不翻译", "none"), ("中文", "zh"), ("English", "en"), ("日本語", "ja")):
        it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, "pickLang:", "")
        it.setTarget_(ctrl)
        it.setRepresentedObject_(f"tr:{code}")
        tr_menu.addItem_(it)
        ctrl._lang_items = (getattr(ctrl, "_lang_items", None) or []) + [(it, f"tr:{code}")]
    mi_tr = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("翻译成", "", "")
    mi_tr.setSubmenu_(tr_menu)
    menu.addItem_(mi_tr)

    menu.addItem_(NSMenuItem.separatorItem())
    try:
        ctrl._refresh_lang_checks()
    except Exception:  # noqa: BLE001
        pass

    mi_key = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "设置翻译 API Key…", "setupTranslateKey:", "")
    mi_key.setTarget_(ctrl)
    menu.addItem_(mi_key)

    # 鲸鱼点开哪个应用
    app_menu = NSMenu.alloc().initWithTitle_("鲸鱼打开的应用")
    for title, spec in (("DeepSeek Harness", "DeepSeek Harness"),
                        ("Safari", "Safari"),
                        ("终端", "Terminal"),
                        ("访达", "Finder"),
                        ("自定义…", "custom")):
        it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, "pickApp:", "")
        it.setTarget_(ctrl)
        it.setRepresentedObject_(spec)
        app_menu.addItem_(it)
    mi_app = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("鲸鱼打开的应用", "", "")
    mi_app.setSubmenu_(app_menu)
    menu.addItem_(mi_app)

    try:
        ctrl._refresh_tr_enabled()
    except Exception:  # noqa: BLE001
        pass

    menu.addItem_(NSMenuItem.separatorItem())

    mi_to_mon = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "切到耳机监听（手动）", "switchToMonitor:", "")
    mi_to_mon.setTarget_(ctrl)
    menu.addItem_(mi_to_mon)

    mi_to_spk = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "切回扬声器（手动）", "switchToSpeaker:", "")
    mi_to_spk.setTarget_(ctrl)
    menu.addItem_(mi_to_spk)

    menu.addItem_(NSMenuItem.separatorItem())

    mi_quit = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "退出小鲸鱼", "quitApp:", "q")
    mi_quit.setTarget_(ctrl)
    menu.addItem_(mi_quit)

    item.setMenu_(menu)
    ctrl.status_item = item
    ctrl.status_menu_item = mi_toggle
    log("菜单栏图标已装上")
    return item


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    ctrl = Controller.alloc().init()
    bar = build_bar(ctrl)
    ctrl.touch_bar = bar
    ctrl.bar_visible = False

    install_status_item(ctrl)

    # 先试一下激活自己，看能不能让触摸条接受点击
    try:
        app.activateIgnoringOtherApps_(True)
        log("已尝试 activateIgnoringOtherApps")
    except Exception as e:  # noqa: BLE001
        log(f"激活失败 {e}")

    ctrl._present()
    ctrl.bar_visible = True

    # 默认不启动采集：避免一直占用音频输入设备（用户在意麦克风指示）
    ctrl._meter_on = False
    try:
        ctrl.level_label.setHidden_(True)
        ctrl.meter_menu_item.setTitle_("打开音量律动")
    except Exception:  # noqa: BLE001
        pass
    log("启动完成（律动默认关闭，需要时点菜单开启）")
    _mt = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
        0.12, ctrl, "tickMeter:", None, True)
    NSRunLoop.mainRunLoop().addTimer_forMode_(_mt, NSRunLoopCommonModes)
    log(f"timer={_mt} valid={_mt.isValid()} respondsTick={ctrl.respondsToSelector_('tickMeter:')}")

    import threading as _th
    _th.Thread(target=ctrl._subtitle_reader, daemon=True).start()

    ctrl.refreshWeather_(None)
    _wt = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
        600.0, ctrl, "refreshWeather:", None, True)
    NSRunLoop.mainRunLoop().addTimer_forMode_(_wt, NSRunLoopCommonModes)
    log("进 runloop，PID=%d" % os.getpid())
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
