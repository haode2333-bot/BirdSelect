# -*- coding: utf-8 -*-
"""toolpaths.py — 运行时路径解析：exiftool 位置 + snipe 模型位置。

让观鸟工具在两种环境都能跑，无需改动调用方逻辑：

1. 开发机：venv + snipe init 下载的模型（在 %APPDATA%/.snipe/models/），
   exiftool 在 PATH 里。
2. 便携版：exiftool.exe 和 models/ 都随包放在工具目录下，双击 bat/exe 即可用，
   发给没装 Python/exiftool/模型 的电脑也能直接跑。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def app_dir() -> Path:
    """工具所在目录。

    - 源码运行：本文件所在目录（bird_pick.py / bird_desktop.py 的同级目录）
    - PyInstaller 打包后：exe 所在目录
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def find_exiftool() -> str | None:
    """优先找工具目录下自带的 exiftool，否则退回系统 PATH。"""
    exe = "exiftool.exe" if os.name == "nt" else "exiftool"
    for candidate in (app_dir() / "exiftool" / exe, app_dir() / exe):
        if candidate.exists():
            return str(candidate)
    return shutil.which("exiftool")


def resolve_model_paths() -> tuple[Path, Path]:
    """返回 (detector_model, classifier_model) 的绝对路径。

    优先用工具目录下随包分发的 models/（便携版），否则退回
    %APPDATA%/.snipe/models/（开发机 snipe init 下载的模型）。
    """
    local = app_dir() / "models"
    detector = local / "yolo26x-seg.onnx"
    classifier = local / "classifier.onnx"
    if detector.exists() and classifier.exists():
        return detector, classifier

    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    cfg_models = Path(appdata) / ".snipe" / "models"
    return cfg_models / "yolo26x-seg.onnx", cfg_models / "classifier.onnx"
