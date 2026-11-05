#!/bin/zsh
# 双击运行：在本目录启动 Touch Bar 小鲸鱼
cd "$(dirname "$0")" || exit 1

# 找一个装了 pyobjc 的 python
for py in "$HOME/.local/conda/bin/python" "$(command -v python3)"; do
  [ -x "$py" ] || continue
  if "$py" -c "import objc, AppKit" 2>/dev/null; then
    exec "$py" whale_touchbar.py
  fi
done

echo "没找到装了 PyObjC 的 Python 3。"
echo "先跑：python3 -m pip install -r requirements.txt"
echo
echo "按任意键关闭…"; read -k1 -s
