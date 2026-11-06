# 鲸鱼条 · WhaleBar

A tiny Touch Bar widget for old MacBook Pros: weather, volume meter, live subtitles
with translation. Built by a non-programmer, with DeepSeek.

> 一个给老款 MacBook Pro 的 Touch Bar 小组件：天气、音量律动、实时字幕（可翻译）。
> 由**一个不会写代码的人**，靠 DeepSeek 一句一句聊出来的。

---

## 关于我（中文）

我不是程序员。就是个刚毕业、上班的普通打工人，**代码没学过**，纯属感兴趣，正自己摸着学。
这整个项目，是**用 DeepSeek（V4.1 Flash 1017）+ DeepSeek Harness** 一行行聊出来的——
我提想法，它写代码，我试，报错再贴给它。

手头这台是 **2019 款 MacBook Pro**，Touch Bar 我一直觉得挺好玩，闲下来就想给它做点东西。
但过阵子因为个人经济原因，这台电脑大概要卖掉，所以只能上传一个**半成品**：
它不是产品，是个**思路**。谁看着有意思，拿走接着做就行。

做得不好的地方，尽管指正。

## About me (English)

I'm an ordinary office worker from China, just out of university. I don't know how to
code and never studied it — I'm just interested and learning by doing.

Everything here was built by chatting with **DeepSeek (V4.1 Flash 1017)** through
**DeepSeek Harness**: I described what I wanted, it wrote the code, I ran it, pasted the
errors back.

I have a 2019 MacBook Pro and always liked its Touch Bar, so I started playing with it.
But I'll probably have to sell this machine for financial reasons, so I'm uploading a
**work in progress** — not a polished product, just an idea worth sharing.
Any corrections or improvements are very welcome.

---

## 它是什么

![Touch Bar 实拍](docs/screenshot.jpg)

Touch Bar 上占一整行，从左到右是这个样子：

```
[ 重庆 17° 小毛毛雨 ]   [ 律动条 / 字幕 ]      [ 🐳 ]  [ 收 ]
```

- **左边天气**：按定位自动取本地天气，点一下打开系统天气 App
- **中间那块空白**：可以开「音量律动」（跟着系统声音跳），也可以开「字幕」
- **右边鲸鱼**：点一下打开你指定的 app（默认 DeepSeek Harness，可以换成任何应用）
- **收**：把整条藏起来，还你原生 Touch Bar

两个功能（律动 / 字幕）**互斥**，开一个会自动关掉另一个。

### 字幕能干什么

- 实时把系统正在播的声音转成文字，显示在 Touch Bar 上
- 支持 **中文 / 英文 / 日语 / 韩语** 四种识别语言
- 可以**自动翻译**成另一种语言（中文、英文、日语）

**用来看生肉视频挺合适**：电影、纪录片、外文讲座这种，一般没问题。
但**不适合听歌**——唱歌的歌词任何语音识别都很难，别抱期待。
另外字幕有一点点延迟（1~2 秒），追求逐帧同步的话不合适。

### 翻译用的什么

- **国内**：百度翻译开放平台，自己申请 API，免费额度每月 5 万字
- **海外**：代码里也留了 `google_free`（不用 key），但**我没实测过**，而且这个接口在国内连不上

翻译这块我做得比较糙，**有能力的朋友可以自己接更好的**（DeepL、腾讯云都行，`translate.py` 里留了位置）。

## 目前的完成度

说实话：**半成品**。

能用的部分：天气、律动、字幕、翻译、语言切换、快捷键。
没做完的：设置界面、更多语言、打包成独立 app、歌词识别。

我自己是拿来日常用的，够用。但离"产品"还有距离——留着给愿意接手的人。

## 不需要装虚拟声卡

律动和字幕都直接用 **ScreenCaptureKit** 抓系统音频，不装 BlackHole、不切系统输出。

代价是需要授权（第一次运行会弹窗）：

```
系统设置 → 隐私与安全性
  ├─ 屏幕录制      （抓系统声音）
  └─ 语音识别      （把声音转成文字）
```

## 安装

```bash
# 1. 装依赖
python3 -m pip install -r requirements.txt

# 2. 生成两个 helper 的 .app（必须先打包，否则拿不到权限）
./build.command

# 3. 配置
cp config.example.json config.json
$EDITOR config.json          # 至少改 appName

# 4. 启动
python3 whale_touchbar.py
# 或者双击 启动.command
```

## 快捷键

| 快捷键 | 作用 |
|---|---|
| `⌘ ⌥ S` | 开关字幕 |
| `⌘ ⌥ M` | 开关音量律动 |
| `⌘ ⌥ H` | 显示 / 隐藏整条 |

用 Carbon 的 `RegisterEventHotKey`，**不需要辅助功能权限**。
菜单栏图标上会出现一个小圆点，表示字幕或律动正在跑。

## 配置

| 键 | 默认 | 说明 |
|---|---|---|
| `appName` | `DeepSeek Harness` | 点鲸鱼时启动哪个应用 |
| `bar.width` | `980` | 整条宽度（不同机型可调） |
| `weather.refreshSeconds` | `600` | 天气刷新间隔 |
| `subtitle.language` | `zh-CN` | 识别语言：`zh-CN` / `en-US` / `ja-JP` / `ko-KR` |
| `translate.provider` | `none` | `none` / `baidu` |
| `translate.to` | `zh` | 翻成什么语言 |
| `audio.autoSwitchOutput` | `false` | 旧行为：自动切到多输出设备（现在不需要） |

菜单里可以直接选**识别语言**和**翻译成**，改完 2 秒生效，不用重启。

## 翻译（可选）

默认**不翻译**，只显示识别出来的原文。想要翻译，得自己提供一个翻译源——没有内置的免费额度。

### 国内用户：百度翻译

[百度翻译开放平台](https://fanyi-api.baidu.com/) 申请「通用翻译 API」，免费额度每月 5 万字：

```json
"translate": {
  "provider": "baidu",
  "appid": "你的APPID",
  "key": "你的密钥",
  "from": "auto",
  "to": "zh"
}
```

免费版 **QPS = 1**，所以代码里做了两件事：限制请求间隔、把多句合并成一次请求。

### 海外用户：Google 网页接口

百度在国内很好用，但**服务在境外、需要中国手机号注册**，海外用不了。
国外可以用代码里已经实现好的 `google_free`（无需 key，走网页接口）：

```json
"translate": {
  "provider": "google_free",
  "from": "auto",
  "to": "zh"
}
```

> 这是**非官方接口**：可能限流、可能随时失效，别当生产用。
> 反过来，这个接口在**中国大陆基本连不上**，所以两边的用户各用各的。

### 想更稳的话

自己接 DeepL / Google Cloud / 腾讯云的正式 API，改 `translate.py` 里的
`_enabled()` 和 `translate()` 加一个分支就行，结构是留好的。

## 已知限制

- 只有 Touch Bar 机型能用
- 字幕有 1~2 秒延迟
- **唱歌识别不了**——带伴奏的唱词，ASR 基本都一样烂
- 日语/韩语**没有本地识别模型**，必须联网
- 翻译依赖第三方 API，断网或超额度就只显示原文

## 踩过的坑（都写进代码注释了）

| 现象 | 原因 |
|---|---|
| 一个回调都不来 | `SCStream` 被 GC 回收，必须一直持有 |
| 有数据但读不出来 | PyObjC 的 `CMSampleBufferRef` 没有 `.dataBuffer()`，要用 `CMSampleBufferGetDataBuffer()` |
| 数据被丢弃 | `CMBlockBufferCopyDataBytes` 返回 `(状态, buffer)` 元组，不是单个状态码 |
| 识别成乱码 | SCK 音频是**平面布局**（前 N 左声道、后 N 右声道），不是交错的 |
| 切几次就彻底不识别 | 识别任务必须在**主线程**创建，后台线程建会一直报 209 |
| 字幕只剩一个句号 | 按长度切 delta 会把句子切碎，要等文本稳定后整句处理 |
| 菜单项是灰的 | `@python_method` 装饰器会让 ObjC 看不见这个方法 |

## TODO / 欢迎接手

- [ ] 设置界面（现在只能手改 JSON）
- [ ] 更多翻译源（DeepL / 腾讯 / 本地模型）
- [ ] Apple Silicon + 外接 Touch Bar 的验证
- [ ] 打包成自包含 .app（要绑 Python，体积会大）

## 许可

MIT，见 `LICENSE`。
