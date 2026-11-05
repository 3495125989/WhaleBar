"""翻译接口（云 API）。

设计原则：
- **可换**：读 config 里的 provider，不绑定某一家
- **能降级**：没 key / 出错 / 超时 → 返回 None，调用方就只显示原文，不崩
- **缓存**：同一句不重复请求（视频里重复的台词很多）

目前实现两种：
  provider = "none"   → 不翻译（默认）
  provider = "baidu"  → 百度翻译通用文本 API

config.json:
  "translate": {
    "provider": "baidu",
    "appid": "...",
    "key": "...",
    "from": "auto",      // 源语言：auto / en / ja / ko ...
    "to": "zh"
  }
"""

import hashlib
import json
import random
import threading
import time

# 百度用的语言码和我们界面上写的不一样（日语是 jp 不是 ja）
BAIDU_LANG = {
    "zh": "zh", "en": "en", "ja": "jp", "ko": "kor",
    "fr": "fra", "es": "spa", "de": "de", "ru": "ru",
    "auto": "auto",
}

_last_call = [0.0]      # 百度免费版 QPS=1，必须限速
_lock = threading.Lock()
import urllib.parse
import urllib.request

_cfg = {}
_cache = {}
MAX_CACHE = 500


def configure(section):
    """由主程序在启动时调用，把 config 里的 translate 段塞进来。"""
    global _cfg
    _cfg = dict(section or {})


def _enabled():
    provider = _cfg.get("provider") or "none"
    if provider == "none":
        return False
    if provider == "google_free":
        return True            # 不需要 key
    return bool(_cfg.get("appid")) and bool(_cfg.get("key"))


def _google_free(text, src_lang, dst_lang):
    """谷歌翻译的公开网页接口，**不需要 key**。

    非官方接口：可能限流、可能随时失效，但个人自用够。
    台词重复多，靠缓存能挡掉大部分请求。
    """
    sl = "auto" if src_lang in ("auto", "", None) else src_lang
    q = urllib.parse.urlencode({
        "client": "gtx", "sl": sl, "tl": dst_lang, "dt": "t", "q": text,
    })
    url = "https://translate.googleapis.com/translate_a/single?" + q
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=6) as r:
        data = json.loads(r.read().decode("utf-8"))
    try:
        return "".join(part[0] for part in data[0] if part and part[0])
    except Exception:  # noqa: BLE001
        return None


def _baidu(text, src_lang, dst_lang):
    appid = _cfg.get("appid", "")
    key = _cfg.get("key", "")
    salt = str(random.randint(32768, 65536))
    sign_src = appid + text + salt + key
    sign = hashlib.md5(sign_src.encode("utf-8")).hexdigest()

    params = urllib.parse.urlencode({
        "q": text,
        "from": src_lang,
        "to": dst_lang,
        "appid": appid,
        "salt": salt,
        "sign": sign,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://fanyi-api.baidu.com/api/trans/vip/translate",
        data=params,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=6) as r:
        data = json.loads(r.read().decode("utf-8"))

    if "error_code" in data:
        raise RuntimeError(f"baidu error {data['error_code']}: {data.get('error_msg')}")
    results = data.get("trans_result") or []
    if not results:
        return None
    return "".join(item.get("dst", "") for item in results)


def _throttle():
    """保证两次请求间隔 ≥ 1.1 秒（免费版 QPS=1，超了报 54003）。"""
    with _lock:
        wait = 1.1 - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()


def enabled():
    return _enabled()


def translate_batch(texts):
    """一次请求翻多句（用 \n 分隔）——免费版 QPS=1，批量能把额度用满。"""
    texts = [t.strip() for t in texts if t and t.strip()]
    if not texts or not _enabled():
        return None
    joined = "\n".join(texts)
    src0 = BAIDU_LANG.get(_cfg.get("from", "auto"), "auto")
    dst0 = BAIDU_LANG.get(_cfg.get("to", "zh"), "zh")
    ck0 = f"{src0}|{dst0}|{joined}"
    if ck0 in _cache:
        return _cache[ck0].split("\n")
    src = BAIDU_LANG.get(_cfg.get("from", "auto"), _cfg.get("from", "auto"))
    dst = BAIDU_LANG.get(_cfg.get("to", "zh"), _cfg.get("to", "zh"))
    provider = _cfg.get("provider", "none")
    try:
        _throttle()
        if provider == "baidu":
            try:
                out = _baidu(joined, src, dst)
            except RuntimeError as e:
                if "54003" in str(e):
                    time.sleep(1.5)
                    out = _baidu(joined, src, dst)
                else:
                    raise
        else:
            return None
    except Exception as exc:  # noqa: BLE001
        print(f"[translate] 批量失败：{exc}", flush=True)
        return None
    if not out:
        return None
    if len(_cache) > MAX_CACHE:
        _cache.clear()
    _cache[ck0] = out
    return out.split("\n")


def translate(text):
    """返回译文；任何异常/未启用都返回 None（调用方只显示原文）。"""
    text = (text or "").strip()
    if not text or not _enabled():
        return None
    src = BAIDU_LANG.get(_cfg.get("from", "auto"), _cfg.get("from", "auto"))
    dst = BAIDU_LANG.get(_cfg.get("to", "zh"), _cfg.get("to", "zh"))
    provider = _cfg.get("provider", "none")

    ck = f"{src}|{dst}|{text}"
    if ck in _cache:
        return _cache[ck]

    out = None
    try:
        if provider == "baidu":
            _throttle()
            try:
                out = _baidu(text, src, dst)
            except RuntimeError as e:
                if "54003" in str(e):      # 限流，等 1.5 秒再来一次
                    time.sleep(1.5)
                    out = _baidu(text, src, dst)
                else:
                    raise
        elif provider == "google_free":
            out = _google_free(text, src, dst)
        else:
            return None
    except Exception as exc:  # noqa: BLE001
        print(f"[translate] 失败：{exc}", flush=True)
        return None

    if out:
        if len(_cache) > MAX_CACHE:
            _cache.clear()
        _cache[ck] = out
    return out
