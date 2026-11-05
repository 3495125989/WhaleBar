"""配置加载。

读同目录下的 config.json；没有就用默认值，并提示从 config.example.json 复制一份。
所有可调的东西都从这里走，代码里不再写死路径、设备名、应用名。
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
EXAMPLE_PATH = os.path.join(HERE, "config.example.json")

DEFAULTS = {
    # 点鲸鱼图标时要打开的应用（macOS 上的应用名，不带 .app）
    "appName": "DeepSeek Harness",
    # 点天气时打开的系统应用
    "weatherAppName": "Weather",
    "weather": {
        "enabled": True,
        "refreshSeconds": 600,
    },
    "meter": {
        "enabled": False,
        # 回环设备名（装了 BlackHole 之后叫这个）
        "device": "BlackHole 2ch",
        # 切回哪个输出设备（名字里含这个关键字即可）
        "outputHint": "耳机监听",
    },
    "subtitle": {
        "enabled": False,
        "language": "zh-CN",
        # 字幕文本超过这么多秒没更新就不显示
        "showSeconds": 8,
    },
    # 常驻触摸条的标识符，随便取，别和别人撞
    "trayId": "com.example.whaletouchbar.tray",
    # helper 进程写交互文件的位置
    "tempDir": "/tmp",
    # 记住上次用的真实输出设备名（切回来用）
    "audio": {"lastOutputName": ""},
}


def _merge(base, override):
    """递归合并：override 有的键覆盖 base，没有的保留默认。"""
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load():
    """读配置；文件不存在时提示一次（不中断）。"""
    if not os.path.exists(CONFIG_PATH):
        if os.path.exists(EXAMPLE_PATH):
            print(
                f"[config] 没找到 {os.path.basename(CONFIG_PATH)}，正在使用默认配置。\n"
                f"[config] 建议：cp {os.path.basename(EXAMPLE_PATH)} "
                f"{os.path.basename(CONFIG_PATH)}，然后按需修改。",
                file=sys.stderr,
            )
        return dict(DEFAULTS)

    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            user = json.load(f)
    except Exception as exc:  # noqa: BLE001
        print(f"[config] {CONFIG_PATH} 解析失败：{exc}\n[config] 已回退到默认配置。",
              file=sys.stderr)
        return dict(DEFAULTS)

    return _merge(DEFAULTS, user)


CFG = load()


def path(name):
    """返回 tempDir 下的交互文件路径。"""
    return os.path.join(CFG["tempDir"], name)


def reload():
    """从磁盘重新读一遍（别用进程里过期的副本覆盖用户的修改）。"""
    global CFG
    CFG = load()
    return CFG


def save():
    """把当前 CFG 写回 config.json（只用于记住少量状态）。"""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(CFG, f, ensure_ascii=False, indent=2)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[config] 写回失败：{exc}", file=sys.stderr)
        return False
