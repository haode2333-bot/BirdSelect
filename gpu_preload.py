# -*- coding: utf-8 -*-
"""GPU 加速预加载 + snipe 日志降噪 + 后端检测。

后端自动选择：
  - Windows/NVIDIA：onnxruntime-gpu + CUDA。CUDA provider 依赖的 NVIDIA 运行库
    （cublas/cudnn/cudart）装在 site-packages/nvidia/*/bin，不在 DLL 搜索路径，
    需手动 onnxruntime.preload_dlls()，否则静默回退 CPU。
  - macOS Apple Silicon：CoreMLExecutionProvider（走 ANE/Metal 加速），无需预加载。
  - 其他：CPU。

用法：在 snipe 导入之后、首次 detect_birds 之前调用 ensure_gpu() 即可。
"""

import sys

_preloaded = False
_backend = "unknown"


def suppress_snipe_logging():
    """关掉 snipe(loguru) 默认的 DEBUG/INFO stderr 输出，只保留 ERROR。

    snipe 用 loguru 模块级 logger（挂在根 logger 上），默认往 stderr 打 DEBUG
    级别日志，每张图几十行（bbox 去重、GPS 过滤全在打）。观鸟工具 GUI 把子进程
    stderr 并进 stdout 显示，这些噪音会刷爆日志框。这里摘掉 loguru 默认 handler，
    只留 ERROR 级，让日志只剩 bird_pick 自己打印的鸟种概要行。
    """
    try:
        from loguru import logger
        logger.remove()  # 摘掉默认 stderr handler（DEBUG 级）
        logger.add(sys.stderr, level="ERROR")  # 只保留真正的错误
    except Exception:
        pass


def detect_backend(force_cpu: bool = False) -> str:
    """返回当前可用的最快推理后端名：'CUDA' / 'CoreML' / 'CPU'。"""
    if force_cpu:
        return "CPU"
    try:
        import onnxruntime as ort
        providers = ort.get_available_providers()
        if "CUDAExecutionProvider" in providers:
            return "CUDA"
        if "CoreMLExecutionProvider" in providers:
            return "CoreML"
    except Exception:
        pass
    return "CPU"


def ensure_gpu(force_cpu: bool = False) -> str:
    """预加载 NVIDIA 运行库 DLL（Windows），让识别真正走 CUDA。返回后端名。

    - 成功加载 CUDA DLL 且 CUDA EP 可用 → 'CUDA'
    - Apple Silicon 有 CoreML EP → 'CoreML'（ANE/Metal，无需预加载）
    - 无加速 / 加载失败 / force_cpu=True → 'CPU'（识别功能不受影响）
    """
    global _preloaded, _backend
    if _preloaded:
        return _backend

    backend = detect_backend(force_cpu)
    if backend == "CUDA":
        try:
            import onnxruntime as ort
            if "CUDAExecutionProvider" in ort.get_available_providers():
                ort.preload_dlls()
        except Exception:
            backend = "CPU"

    _backend = backend
    _preloaded = True
    return backend


def backend_label(backend: str) -> str:
    """后端名 → 给用户看的中文标签。"""
    return {
        "CUDA": "GPU 加速（NVIDIA CUDA）",
        "CoreML": "GPU 加速（Apple Silicon / Metal）",
        "CPU": "CPU 模式",
    }.get(backend, backend)
