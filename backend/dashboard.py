"""
相容層（Compatibility shim）
--------------------------------------------------
Railway 上面嘅服務設定可能仍然寫住舊版嘅啟動指令：

    gunicorn dashboard:app

v1 嘅 `dashboard.py` 係放 Flask app 嘅檔案，v2 已經合併入 `main.py`，
所以嗰句舊指令會爆 `ModuleNotFoundError: No module named 'dashboard'`。

呢個檔案就係為咗令嗰句舊指令繼續行得通 —— 唔會再 crash。
真正嘅 app 定義喺 main.py，呢度只係轉出去。

建議：喺 Railway 將 Start Command 改做 `python main.py`（或者清空佢），
      就會用返 Procfile / railway.json 嘅設定，更加乾淨。
"""
from main import app  # noqa: F401

__all__ = ["app"]
