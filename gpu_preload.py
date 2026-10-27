# -*- coding: utf-8 -*-
"""GPU 加速预加载。

观鸟工具用 onnxruntime-gpu 做识别，但 onnxruntime 的 CUDA provider 依赖的
NVIDIA 运行库（cublas/cudnn/cudart 等）装在 site-packages/nvidia/*/bin 目录下，
不在 Windows 的 DLL 搜索路径里。不手动调用 onnxruntime.preload_dlls() 的话，
onnxruntime 会静默回退到 CPU（识别照常，但速度没提升）。snipe 自身不会调这个，
所以在这里补上。

用法：在 snipe 导入之后、首次 detect_birds 之前调用一次 ensure_gpu() 即可。
"""

_preloaded = False


def ensure_gpu():
    """预加载 NVIDIA 运行库 DLL，让识别真正走 CUDA。失败则静默回退 CPU，不阻塞。"""
    global _preloaded
    if _preloaded:
        return
    try:
        import onnxruntime as ort  # noqa: PLC0415
        ort.preload_dlls()
    except Exception:
        # 没装 GPU 运行库 / 无显卡 / 加载失败时，回退 CPU，识别功能不受影响
        pass
    _preloaded = True
