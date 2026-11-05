"""音量律动助手：用 ScreenCaptureKit 抓系统声音 → 写 /tmp/whale_level

不依赖 BlackHole，也不切换系统输出。必须在 .app 里跑（TCC 认 bundle）。
数值算法与旧版一致：RMS → dB → 0..1 → 快起慢落。
"""

import math
import os
import time

from sck_audio import SystemAudio, read_asbd, samples, to_mono

OUT = os.path.join(os.environ.get("WHALE_TMP", "/tmp"), "whale_level")
LOG = "/tmp/whale-meter.log"

smooth = 0.0
fmt = {}


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def write_atomic(text, path=OUT):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def on_sample(sample):
    global smooth
    vals, frames = samples(sample)
    if not vals:
        return
    info = fmt.get("info") or read_asbd(sample)
    fmt["info"] = info
    mono = to_mono(vals, frames, info["ch"], info["planar"])
    rms = math.sqrt(sum(v * v for v in mono) / max(1, len(mono)))

    db = 20.0 * math.log10(rms + 1e-9)
    target = min(1.0, max(0.0, (db + 45.0) / 45.0)) ** 0.85
    if target > smooth:
        smooth += (target - smooth) * 0.65
    else:
        smooth += (target - smooth) * 0.18
    write_atomic(f"{smooth:.4f}")


def main():
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    write_atomic("0.0000")

    cap = SystemAudio(on_sample, log=log)
    cap.start(on_error=lambda e: log(f"❌ 拿不到内容：{e}"))

    from PyObjCTools import AppHelper
    AppHelper.runConsoleEventLoop(installInterrupt=True)


if __name__ == "__main__":
    main()
