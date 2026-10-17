"""tesseract 執行檔自動檢查／修復（2026-10-15）。

背景：Railway 用 nixpacks 建置，靠 nixpacks.toml 嘅 aptPkgs 裝 tesseract。
但建置快取、builder 設定或部署根目錄一有風吹草動，個執行檔就會唔見 ——
用戶 2026-10-15 就遇上：Pillow ✅、pytesseract ✅，但 tesseract 執行檔 ❌，
整個 OCR 停擺，而且佢喺手機上完全唔知可以做乜。

所以呢個模組喺啟動時自己檢查一次，唔見就想辦法裝返，令 OCR 有自我修復能力。
"""
import os
import shutil
import subprocess
import sys

_CANDIDATES = (
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/nix/var/nix/profiles/default/bin/tesseract",
    "/root/.nix-profile/bin/tesseract",
    "/usr/share/tesseract-ocr/5/tesseract",  # 部分 Debian 映像
)


def find() -> str:
    """搵 tesseract 執行檔；搵唔到回空字串。"""
    p = shutil.which("tesseract")
    if p:
        return p
    for c in _CANDIDATES:
        if os.path.exists(c) and os.access(c, os.X_OK):
            return c
    return ""


def _apt_install() -> bool:
    """喺容器入面即時裝 tesseract（需要 root；失敗唔會拋出）。"""
    if os.geteuid() != 0:
        return False
    for cmd in (
        ["apt-get", "update", "-qq"],
        ["apt-get", "install", "-y", "-qq", "--no-install-recommends",
         "tesseract-ocr", "tesseract-ocr-eng"],
    ):
        try:
            subprocess.run(cmd, check=False, timeout=300,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001
            return False
    return bool(find())


def ensure() -> dict:
    """確保 tesseract 可用；回傳診斷字典。"""
    path = find()
    if path:
        return {"ok": True, "path": path, "action": "已有"}

    # 試下加到 PATH（有時安裝咗但 PATH 冇）
    for d in ("/usr/local/bin", "/nix/var/nix/profiles/default/bin"):
        if os.path.isdir(d) and d not in os.environ.get("PATH", ""):
            os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + d
    path = find()
    if path:
        return {"ok": True, "path": path, "action": "加咗入 PATH"}

    if _apt_install():
        path = find()
        if path:
            return {"ok": True, "path": path, "action": "即時安裝成功"}

    return {"ok": False, "path": "",
            "action": "裝唔到",
            "hint": ("Railway 建置時冇裝 tesseract。請喺 Railway → Settings 確認 "
                     "Root Directory 設為 backend，然後 Redeploy（建議清埋建置快取）。"
                     "詳情見 Yukie/TyLove_trading_v4/TyLove_OCR修復指示.md")}


if __name__ == "__main__":
    print(ensure())
    sys.exit(0)
