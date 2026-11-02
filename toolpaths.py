# -*- coding: utf-8 -*-
"""toolpaths.py — 运行时路径解析 + 跨平台检测。

让观鸟工具在多种环境都能跑，无需改动调用方逻辑：

1. 开发机：venv + snipe init 下载的模型（在 %APPDATA%/.snipe/models/），exiftool 在 PATH。
2. 便携版：exiftool.exe 和 models/ 都随包放在工具目录下，双击 bat/exe 即可用。
3. macOS：exiftool 走 brew（perl 脚本，在 PATH），模型走 ~/.snipe/models/ 或随包 models/。

另含：
- 统一的 RAW/位图扩展名列表（供扫描器复用）
- Mac / Apple Silicon 检测
- rawpy 缩略图提取（跨品牌 RAW 预览兜底，Apple Silicon 上为原生 arm64 加速）
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# 支持的 RAW 扩展名（小写；扫描时大小写都兼容）
RAW_EXTENSIONS = (
    ".cr3", ".cr2",      # 佳能
    ".nef", ".nrw",      # 尼康
    ".arw", ".sr2",      # 索尼
    ".raf",              # 富士
    ".rw2",              # 松下
    ".orf",              # 奥林巴斯 / OM
    ".pef", ".dng",      # 宾得 / 通用 DNG
    ".rwl",              # 徕卡
)
BITMAP_EXTENSIONS = (".jpg", ".jpeg", ".png")
IMAGE_EXTENSIONS = RAW_EXTENSIONS + BITMAP_EXTENSIONS

# 扫描用 glob 模式（大小写都列全）
SCAN_GLOBS = tuple(f"*{e}" for e in IMAGE_EXTENSIONS) + \
             tuple(f"*{e.upper()}" for e in IMAGE_EXTENSIONS)


def is_raw(path: Path | str) -> bool:
    return Path(path).suffix.lower() in RAW_EXTENSIONS


def app_dir() -> Path:
    """工具所在目录。

    - 源码运行：本文件所在目录（bird_pick.py / bird_desktop.py 的同级目录）
    - PyInstaller 打包后：exe 所在目录
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def is_mac() -> bool:
    return sys.platform == "darwin"


def is_apple_silicon() -> bool:
    """M 系列芯片（arm64）检测。"""
    return is_mac() and platform.machine().lower() in ("arm64", "aarch64")


def find_exiftool() -> str | None:
    """优先找工具目录下自带的 exiftool，否则退回系统 PATH。

    - Windows：找 exiftool/exiftool.exe 或工具目录下 exiftool.exe
    - macOS/Linux：找 exiftool/exiftool（brew 装的 perl 脚本在 PATH）
    """
    exe = "exiftool.exe" if os.name == "nt" else "exiftool"
    for candidate in (app_dir() / "exiftool" / exe, app_dir() / exe):
        if candidate.exists():
            return str(candidate)
    return shutil.which("exiftool")


def resolve_model_paths() -> tuple[Path, Path]:
    """返回 (detector_model, classifier_model) 的绝对路径。

    优先用工具目录下随包分发的 models/（便携版），否则退回
    %APPDATA%/.snipe/models/（开发机 snipe init 下载的模型）；
    macOS 下 %APPDATA% 不存在，退回 ~/.snipe/models/。
    """
    local = app_dir() / "models"
    detector = local / "yolo26x-seg.onnx"
    classifier = local / "classifier.onnx"
    if detector.exists() and classifier.exists():
        return detector, classifier

    if os.name == "nt":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        cfg_models = Path(appdata) / ".snipe" / "models"
    else:
        cfg_models = Path.home() / ".snipe" / "models"
    return cfg_models / "yolo26x-seg.onnx", cfg_models / "classifier.onnx"


def no_window_flags() -> int:
    """Windows 下返回 CREATE_NO_WINDOW，防止调用 exiftool 等控制台程序时弹黑窗。

    GUI（pythonw.exe，无控制台）里 subprocess 调用控制台子系统 exe 时，
    不加这个标志会为每次调用新建一个 cmd 窗口，批量处理时会疯狂弹窗。
    macOS/Linux 无此问题，返回 0。
    """
    if os.name == "nt":
        return subprocess.CREATE_NO_WINDOW  # 0x08000000
    return 0


def extract_thumb_via_rawpy(path: Path) -> bytes | None:
    """用 rawpy（libraw）提取 RAW 内嵌缩略图，返回 JPEG bytes。

    跨品牌兜底：富士 RAF / 奥林巴斯 ORF / 松下 RW2 等格式的 JpgFromRaw 标签
    名称不一，rawpy 统一走 libraw，最稳。Apple Silicon 上 libraw 是原生 arm64
    构建，比 exiftool（perl 脚本）快得多，适合批量预览提取。
    失败返回 None（调用方回退）。
    """
    try:
        import rawpy
    except ImportError:
        return None
    try:
        with rawpy.imread(str(path)) as raw:
            thumb = raw.extract_thumb()
        if thumb.format == rawpy.ThumbFormat.JPEG:
            return bytes(thumb.data)
        # 位图缩略图（BMP/RGB）转 JPEG
        import io
        import numpy as np
        from PIL import Image
        arr = np.asarray(thumb.data)
        if arr.ndim == 3:
            arr = arr[:, :, :3]
        buf = io.BytesIO()
        Image.fromarray(arr).convert("RGB").save(buf, format="JPEG", quality=90)
        return buf.getvalue()
    except Exception:
        return None
