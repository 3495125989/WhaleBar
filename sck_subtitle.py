"""字幕助手：ScreenCaptureKit 抓系统音频 → 语音识别 →（可选）翻译 → 写 /tmp/whale_text

不依赖 BlackHole，也不切换系统输出。
必须在 .app 里跑（TCC 只认 bundle），首次需要授权：
  - 屏幕录制（抓系统声音）
  - 语音识别

几个踩过的坑（别再踩）：
1. `SCStream` 必须一直持有引用，否则被 GC → 一个回调都不来
2. 音频要用 `CMSampleBufferGetDataBuffer()`，PyObjC 对象没有 `.dataBuffer()`
3. `CMBlockBufferCopyDataBytes` 返回 `(状态, buffer)` 元组
4. SCK 的音频是**平面布局**：前 N 个是左声道，后 N 个是右声道
5. SFSpeech 的识别任务必须在**主线程**创建（用 AppHelper.callAfter）
6. 句子要等识别稳定 1.5 秒再整句翻译，别按长度切片（会切碎）
"""

import os
import time
import threading

import AVFoundation as AV
import Speech as SP

import config
import translate
from sck_audio import SystemAudio, read_asbd, samples, to_mono

CFG = config.CFG
translate.configure(CFG.get("translate") or {})

OUT = os.path.join(os.environ.get("WHALE_TMP", "/tmp"), "whale_text")
LOG = "/tmp/whale-subtitle.log"

state = {
    "req": None, "task": None, "rec": None,
    "full": "", "last_change": 0.0, "translated_src": "",
    "err_count": 0, "blocks": 0, "ondevice_fallback": False,
}


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def write_out(text):
    try:
        with open(OUT, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass


# ───────────────────────── 语音识别 ─────────────────────────
def start_recognition():
    locale = state.get("locale") or (CFG.get("subtitle") or {}).get("language", "zh-CN")
    from Foundation import NSLocale
    recognizer = SP.SFSpeechRecognizer.alloc().initWithLocale_(
        NSLocale.localeWithLocaleIdentifier_(locale))
    if recognizer is None:
        log(f"❌ 建不出 {locale} 识别器")
        return

    req = SP.SFSpeechAudioBufferRecognitionRequest.alloc().init()
    req.setShouldReportPartialResults_(True)
    try:
        req.setTaskHint_(1)      # Dictation
    except Exception:  # noqa: BLE001
        pass
    try:
        req.setAddsPunctuation_(True)
    except Exception:  # noqa: BLE001
        pass

    if os.environ.get("WHALE_ONDEVICE") == "1" or state.get("ondevice_fallback"):
        try:
            if recognizer.supportsOnDeviceRecognition():
                req.setRequiresOnDeviceRecognition_(True)
                log("使用本地识别")
        except Exception:  # noqa: BLE001
            pass
    else:
        log("使用服务器识别")

    def on_result(result, error):
        if error is not None:
            log(f"识别出错: {error}")
            if state.get("req") is not None:
                state["req"] = None
                state["err_count"] += 1
                if state["err_count"] >= 3 and not state.get("ondevice_fallback"):
                    state["ondevice_fallback"] = True
                    log("连续失败，切本地识别兜底")
                delay = min(8.0, 0.5 * state["err_count"])

                def later():
                    time.sleep(delay)
                    from PyObjCTools import AppHelper
                    AppHelper.callAfter(start_recognition)

                threading.Thread(target=later, daemon=True).start()
            return

        if result is None:
            return
        text = result.bestTranscription().formattedString()
        if not text:
            return
        state["full"] = text
        state["last_change"] = time.time()
        state["err_count"] = 0
        if not translate.enabled():
            write_out(text[-30:])

    task = recognizer.recognitionTaskWithRequest_resultHandler_(req, on_result)
    state.update(req=req, task=task, rec=recognizer, started=time.time())
    log("识别已启动")


def feed(sample):
    vals, frames = samples(sample)
    if not vals or state.get("req") is None:
        return
    info = state.get("fmt") or read_asbd(sample)
    state["fmt"] = info
    mono = to_mono(vals, frames, info["ch"], info["planar"])

    # 48k → 16k：先 3 点平均（低通）再抽取，直接抽取会混叠
    decim = 3
    n16 = frames // decim
    if n16 <= 0:
        return
    mono16 = [(mono[i * decim] + mono[i * decim + 1] + mono[i * decim + 2]) / 3.0
              for i in range(n16)]

    fmt = AV.AVAudioFormat.alloc().initWithCommonFormat_sampleRate_channels_interleaved_(
        AV.AVAudioPCMFormatFloat32, 16000.0, 1, True)
    buf = AV.AVAudioPCMBuffer.alloc().initWithPCMFormat_frameCapacity_(fmt, n16)
    if buf is None:
        return
    buf.setFrameLength_(n16)
    ch = buf.floatChannelData()
    if ch is None:
        return
    target = ch[0]
    for i in range(n16):
        target[i] = mono16[i]
    state["req"].appendAudioPCMBuffer_(buf)

    state["blocks"] += 1
    if state["blocks"] % 300 == 0:
        log(f"已喂 {state['blocks']} 块")


# ───────────────────────── 翻译 ─────────────────────────
def translate_worker():
    """等识别文本稳定 1.5 秒，整句翻译；没开翻译就显示原文。"""
    while True:
        time.sleep(0.5)
        full = (state.get("full") or "").strip()
        if not full or not translate.enabled():
            continue
        if state.get("translated_src") == full:
            continue
        if time.time() - state.get("last_change", 0) < 1.5:
            continue
        out = translate.translate(full)
        state["translated_src"] = full
        if out:
            write_out(out[-60:])
            log(f"翻译：{full[:24]} → {out[:26]}")
        else:
            write_out(full[-60:])
            log("翻译没结果，显示原文")


# ───────────────────────── 配置热加载 ─────────────────────────
def watch_config():
    path = config.CONFIG_PATH
    last = os.path.getmtime(path) if os.path.exists(path) else 0
    while True:
        time.sleep(2)
        try:
            if not os.path.exists(path):
                continue
            m = os.path.getmtime(path)
            if m == last:
                continue
            last = m
            cfg = config.load()
            locale = (cfg.get("subtitle") or {}).get("language", "zh-CN")
            translate.configure(cfg.get("translate") or {})
            log(f"配置变了 → 识别 {locale}，翻译 {(cfg.get('translate') or {}).get('provider')}")
            old = state.get("task")
            if old is not None:
                try:
                    state["req"].endAudio()
                    old.finish()
                except Exception:  # noqa: BLE001
                    pass
            state.update(req=None, task=None, locale=locale,
                         full="", translated_src="", err_count=0)
            time.sleep(3.0)          # 别和上一次的 endAudio 抢
            from PyObjCTools import AppHelper
            AppHelper.callAfter(start_recognition)
        except Exception as e:  # noqa: BLE001
            log(f"热加载出错 {e}")


def main():
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    cap = SystemAudio(feed, log=log)

    def begin():
        start_recognition()
        threading.Thread(target=translate_worker, daemon=True).start()
        threading.Thread(target=watch_config, daemon=True).start()
        cap.start(on_error=lambda e: log(f"❌ 拿不到内容：{e}"))

    def on_auth(status):
        log(f"语音识别授权: {status}")
        if int(status) == 3:
            begin()
        else:
            log("❌ 没语音识别权限：系统设置 → 隐私与安全性 → 语音识别 里勾上本 app")

    try:
        SP.SFSpeechRecognizer.requestAuthorization_(on_auth)
        log("已请求语音识别授权")
    except Exception as e:  # noqa: BLE001
        log(f"请求授权失败 {e}")
        begin()

    from PyObjCTools import AppHelper
    AppHelper.runConsoleEventLoop(installInterrupt=True)


if __name__ == "__main__":
    main()
