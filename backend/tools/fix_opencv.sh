#!/bin/sh
# 換走 GUI 版 opencv，改用 headless 版（2026-10-04）
#
# 為何要呢步：rapidocr 依賴 opencv-python（非 headless），佢連住 Qt／X11，
# 需要 libGL.so.1、libxcb.so.1 等一串系統庫。喺 Railway 精簡容器上，
# 補完一個又爆下一個（libGL → libxcb → …），唔知仲有幾多個。
#
# opencv-python-headless 係同一個 cv2，但冇 GUI 依賴 ——
# 完全唔需要 libGL／libxcb，問題根源消失，唔再靠系統庫。
#
# ★自癒：如果 headless 版裝完 import 唔到，會自動還原 GUI 版，
#   確保 OCR 唔會因為呢步而完全死掉（最差情況同而家一樣，唔會更差）。
set -u

echo "=== 換走 GUI 版 opencv（避開 libGL／libxcb 系統庫問題）==="

pip install --no-deps --upgrade --force-reinstall opencv-python-headless 2>&1 | tail -3 || true
pip uninstall -y opencv-python opencv-contrib-python 2>&1 | tail -2 || true

if python -c "import cv2" 2>/dev/null; then
    echo "✅ cv2 可用（headless）：$(python -c 'import cv2; print(cv2.__version__)')"
    echo "=== 順便確認 rapidocr 載入得到 ==="
    if python -c "import rapidocr_onnxruntime" 2>/dev/null; then
        echo "✅ rapidocr 載入成功（主引擎就緒）"
    else
        echo "⚠️ rapidocr 仍然載入唔到，稍後由 pytesseract 後備"
    fi
else
    echo "⚠️ headless 版唔得，自動還原 GUI 版"
    pip install --no-deps --upgrade --force-reinstall opencv-python 2>&1 | tail -3 || true
    python -c "import cv2" 2>/dev/null && echo "✅ 已還原 GUI 版 cv2" || echo "❌ cv2 兩版都唔得"
fi

echo "=== 系統庫實況（診斷用）==="
for f in libGL.so.1 libxcb.so.1 libgomp.so.1; do
    if find /usr/lib /lib -name "$f" 2>/dev/null | head -1 | grep -q .; then
        echo "  $f ：✅ 有"
    else
        echo "  $f ：❌ 冇"
    fi
done
