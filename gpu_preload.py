# -*- coding: utf-8 -*-
"""GPU 加速预加载 + snipe 日志降噪。

观鸟工具用 onnxruntime-gpu 做识别，但 onnxruntime 的 CUDA provider 依赖的
NVIDIA 运行库（cublas/cudnn/cudart 等）装在 site-packages/nvidia/*/bin 目录下，
不在 Windows 的 DLL 搜索路径里。不手动调用 onnxruntime.preload_dlls() 的话，
onnxruntime 会静默回退到 CPU（识别照常，但速度没提升）。snipe 自身不会调这个，
所以在这里补上。

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


def ensure_gpu(force_cpu: bool = False) -> str:
    """预加载 NVIDIA 运行库 DLL，让识别真正走 CUDA。返回当前后端名 'CUDA' / 'CPU'。

    - 成功加载 CUDA DLL 且 CUDA EP 可用 → 'CUDA'
    - 无显卡 / 加载失败 / force_cpu=True → 'CPU'（识别功能不受影响）
    """
    global _preloaded, _backend
    if _preloaded:
        return _backend

    backend = "CPU"
    if not force_cpu:
        try:
            import onnxruntime as ort  # noqa: PLC0415
            if "CUDAExecutionProvider" in ort.get_available_providers():
                ort.preload_dlls()
                backend = "CUDA"
        except Exception:
            # 没装 GPU 运行库 / 无显卡 / 加载失败时，回退 CPU
            backend = "CPU"

    _backend = backend
    _preloaded = True
    return backend
