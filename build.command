#!/bin/zsh
# 一键生成两个 helper 的 .app（TCC 只认 bundle，必须打包才能拿权限）
set -e
cd "$(dirname "$0")"
PY="${PYTHON:-$(command -v python3)}"

make_app() {
  local name="$1" bundle="$2" script="$3" desc="$4"
  local app="$name.app"
  rm -rf "$app"
  mkdir -p "$app/Contents/MacOS"
  cat > "$app/Contents/Info.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>run</string>
  <key>CFBundleIdentifier</key><string>$bundle</string>
  <key>CFBundleName</key><string>$name</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSUIElement</key><true/>
  <key>NSScreenCaptureUsageDescription</key><string>$desc</string>
  <key>NSSpeechRecognitionUsageDescription</key><string>把系统声音转成文字显示在触摸条上。</string>
  <key>NSMicrophoneUsageDescription</key><string>仅用于语音识别。</string>
</dict></plist>
PL
  cat > "$app/Contents/MacOS/run" <<RUN
#!/bin/zsh
cd "$(cd "$(dirname "$0")/../../.." && pwd)" || exit 1
exec "$PY" -u "$script" >> "/tmp/whale-$name.log" 2>&1
RUN
  chmod +x "$app/Contents/MacOS/run"
  codesign --force --deep --sign - "$app" >/dev/null 2>&1 || true
  echo "✓ 生成 $app"
}

make_app "字幕助手" "com.example.whaletouchbar.subtitle" "sck_subtitle.py" "读取系统正在播放的声音，用来生成字幕。"
make_app "律动助手" "com.example.whaletouchbar.meter" "sck_meter.py" "读取系统声音，做音量律动。"

echo
echo "好了。第一次运行会弹权限，去 系统设置 → 隐私与安全性 放行：屏幕录制 / 语音识别"
