"""SystemAudio —— 用 ScreenCaptureKit 抓系统声音的公共部分。

律动和字幕两个助手都用这一份，避免各写一遍（之前就因此踩过
"stream 被 GC 回收" 和 "dataBuffer 方法不存在" 两个坑）。

用法：
    from sck_audio import SystemAudio
    cap = SystemAudio(on_samples)          # on_samples(samples, frames, channels, planar)
    cap.start()                             # 包在 .app 里跑才有权限
"""

import ctypes
import struct

import objc
from Foundation import NSObject, NSMutableData
from CoreMedia import (
    CMSampleBufferGetDataBuffer, CMSampleBufferGetNumSamples,
    CMSampleBufferGetFormatDescription,
    CMAudioFormatDescriptionGetStreamBasicDescription,
    CMBlockBufferGetDataLength, CMBlockBufferCopyDataBytes,
)
from ScreenCaptureKit import (
    SCShareableContent, SCStream, SCStreamConfiguration, SCContentFilter,
    SCStreamOutputTypeAudio,
)


def make_queue(name=b"whale.audio"):
    """SCK 需要一个真实的 dispatch_queue 才会回调。"""
    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    lib.dispatch_queue_create.restype = ctypes.c_void_p
    lib.dispatch_queue_create.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
    return objc.objc_object(c_void_p=lib.dispatch_queue_create(name, None))


def read_asbd(sample):
    """读音频格式：采样率 / 声道 / 是否平面布局。"""
    try:
        fd = CMSampleBufferGetFormatDescription(sample)
        d = CMAudioFormatDescriptionGetStreamBasicDescription(fd)
        a = d.contents if hasattr(d, "contents") else d
        return {
            "rate": a.mSampleRate,
            "ch": a.mChannelsPerFrame,
            "planar": bool(a.mFormatFlags & 0x20),   # kAudioFormatFlagIsNonInterleaved
            "is_float": bool(a.mFormatFlags & 0x1),
        }
    except Exception:  # noqa: BLE001
        return {"rate": 48000.0, "ch": 2, "planar": True, "is_float": True}


def samples(sample):
    """把 CMSampleBuffer 变成 (float 列表, 帧数)。

    注意：**必须用 CMSampleBufferGetDataBuffer()**，PyObjC 的
    CMSampleBufferRef 对象没有 .dataBuffer() 方法（那个坑静默失败过）。
    """
    db = CMSampleBufferGetDataBuffer(sample)
    if db is None:
        return None, 0
    length = CMBlockBufferGetDataLength(db)
    if length <= 0:
        return None, 0
    buf = NSMutableData.dataWithLength_(length)
    res = CMBlockBufferCopyDataBytes(db, 0, length, buf)
    st = res[0] if isinstance(res, tuple) else res
    if st != 0:
        return None, 0
    frames = CMSampleBufferGetNumSamples(sample)
    raw = bytes(buf)[:length]
    cnt = len(raw) // 4
    if cnt == 0 or frames <= 0:
        return None, 0
    return struct.unpack(f"<{cnt}f", raw), frames


def to_mono(vals, frames, ch, planar):
    """转单声道。SCK 给的是平面布局：前 frames 左、后 frames 右。"""
    if ch >= 2 and len(vals) >= frames * 2:
        if planar:
            return [(vals[i] + vals[frames + i]) * 0.5 for i in range(frames)]
        return [(vals[i * 2] + vals[i * 2 + 1]) * 0.5 for i in range(frames)]
    return list(vals[:frames])


class _Output(NSObject):
    def init(self):
        self = objc.super(_Output, self).init()
        if self is None:
            return None
        self._cb = None
        return self

    def stream_didOutputSampleBuffer_ofType_(self, stream, sample, of_type):
        if of_type != SCStreamOutputTypeAudio:
            return
        try:
            if self._cb:
                self._cb(sample)
        except Exception:  # noqa: BLE001
            pass


class SystemAudio:
    def __init__(self, on_sample, log=print, sample_rate=48000, channels=2,
                 exclude_self=True):
        self.on_sample = on_sample
        self.log = log
        self.sample_rate = sample_rate
        self.channels = channels
        self.exclude_self = exclude_self
        self._hold = {}          # 必须一直持有，否则被 GC 回收 → 一个回调都不来
        self._running = False

    def start(self, on_error=None):
        def got_content(content, error):
            if error is not None:
                if on_error:
                    on_error(error)
                return
            display = content.displays()[0]

            cfg = SCStreamConfiguration.alloc().init()
            cfg.setCapturesAudio_(True)
            cfg.setSampleRate_(self.sample_rate)
            cfg.setChannelCount_(self.channels)
            cfg.setExcludesCurrentProcessAudio_(self.exclude_self)
            cfg.setWidth_(2)
            cfg.setHeight_(2)

            flt = SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, [])
            out = _Output.alloc().init()
            out._cb = self.on_sample
            stream = SCStream.alloc().initWithFilter_configuration_delegate_(flt, cfg, None)
            q = make_queue()

            self._hold.update(stream=stream, out=out, filter=flt, queue=q, config=cfg)
            ok, err = stream.addStreamOutput_type_sampleHandlerQueue_error_(
                out, SCStreamOutputTypeAudio, q, None)
            self.log(f"注册音频回调: {ok} {err or ''}")

            def started(e):
                self._running = e is None
                if e is not None:
                    self.log(f"❌ 启动失败：{e}")
                    if on_error:
                        on_error(e)
                else:
                    self.log("✓ 开始抓系统声音")

            stream.startCaptureWithCompletionHandler_(started)

        SCShareableContent.getShareableContentWithCompletionHandler_(got_content)

    def stop(self):
        stream = self._hold.get("stream")
        if stream is not None:
            stream.stopCaptureWithCompletionHandler_(lambda e: None)
        self._running = False
