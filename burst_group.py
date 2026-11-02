#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
burst_group.py — 连拍（burst）自动分组。

自动分支：
  1. 佳能（CR2/CR3）：优先读原生连拍标签（BurstUUID 等）。
     读得到 → 按原生标签分组；读不到（如 EOS R8 只有 SequenceNumber=0）→ 回退时间+相似度。
  2. 其余品牌（NEF/ARW/RAF/RW2/ORF/DNG…）：走「时间 + 图像相似度」分组。

时间信号优先用 SubSecTimeOriginal（亚秒级），
比 DateTimeOriginal（秒级）能更准确地切分 20fps 的高速连拍。

相似度信号用 dHash（感知哈希）：只有当相邻两张的时间间隔落在
「暧昧区间」[min_gap, gap_s) 时才计算，避免为几百张图都做哈希。
"""

from __future__ import annotations

import io
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Optional

from toolpaths import no_window_flags, find_exiftool
from rename_engine import read_exif, parse_capture_time

# 连拍切分阈值（秒）
GAP_S = 2.0        # 相邻两张时间差 >= 此值 → 肯定不是同一连拍
MIN_GAP = 0.4      # 时间差 < 此值 → 肯定是同一连拍（高速连拍）
SIM_THRESH = 14    # dHash 距离 <= 此值视为同一连拍（0=完全相同，越大越不像）

CANON_EXT = {".cr2", ".cr3"}


def _is_canon(path: Path) -> bool:
    return path.suffix.lower() in CANON_EXT


# ---------- 图像相似度（dHash 感知哈希） ----------

def _thumb_gray(path: Path) -> Optional["object"]:
    """取一张小灰度缩略图（PIL Image 'L'），失败返回 None。"""
    from PIL import Image
    try:
        if path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
            im = Image.open(path).convert("L")
            im.thumbnail((64, 64))
            return im
        # RAW：优先 rawpy（进程内、快、支持全品牌），失败回退 exiftool ThumbnailImage
        try:
            import rawpy
            with rawpy.imread(str(path)) as raw:
                thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                im = Image.open(io.BytesIO(thumb.data)).convert("L")
            else:
                import numpy as np
                arr = np.asarray(thumb.data)
                if arr.ndim == 3:
                    arr = arr[:, :, :3]
                im = Image.fromarray(arr).convert("L")
            im.thumbnail((64, 64))
            return im
        except Exception:
            pass
        # exiftool ThumbnailImage 兜底
        exif = find_exiftool()
        if not exif:
            return None
        r = subprocess.run([exif, "-b", "-ThumbnailImage", str(path)],
                           capture_output=True, timeout=30,
                           creationflags=no_window_flags())
        if r.returncode == 0 and len(r.stdout) > 1000:
            import io
            im = Image.open(io.BytesIO(r.stdout)).convert("L")
            im.thumbnail((64, 64))
            return im
    except Exception:
        return None
    return None


def _dhash(path: Path, hash_size: int = 8) -> Optional[int]:
    """计算 8x8 dHash，返回 64-bit 整数；取不到图返回 None。"""
    from PIL import Image
    import numpy as np
    im = _thumb_gray(path)
    if im is None:
        return None
    im = im.resize((hash_size + 1, hash_size), Image.LANCZOS)
    arr = np.asarray(im, dtype=np.uint8)
    diff = arr[:, 1:] > arr[:, :-1]
    h = 0
    for b in diff.flatten():
        h = (h << 1) | int(bool(b))
    return h


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


# ---------- 分组 ----------

def group_bursts(files: list[Path], *, gap_s: float = GAP_S,
                 min_gap: float = MIN_GAP, sim_thresh: int = SIM_THRESH) -> dict[str, tuple[int, int, str]]:
    """把 files 分成连拍组。

    返回 {str(resolved_path): (group_id, seq, method)}
      group_id 从 1 开始（按时间先后），seq 为组内序号（1 起），
      method ∈ {'canon_native', 'time_similarity'}。
    """
    files = [Path(f) for f in files if Path(f).exists()]
    if not files:
        return {}

    # 一次批量读所有文件的 EXIF（时间 + 佳能原生标签）
    info: dict[str, dict] = {}
    for f in files:
        info[str(f.resolve())] = read_exif(f)

    canon = [f for f in files if _is_canon(f)]
    others = [f for f in files if not _is_canon(f)]

    result: dict[str, tuple[int, int, str]] = {}
    _group_canon(canon, info, result, gap_s, min_gap, sim_thresh)
    _group_time_sim(others, info, result, gap_s, min_gap, sim_thresh)

    return result


def _group_canon(canon, info, result, gap_s, min_gap, sim_thresh):
    """佳能：先试原生连拍标签，读不到回退时间+相似度。"""
    if not canon:
        return
    # 看是否有可用的 BurstUUID（CR3 的原生连拍标识）
    uuid_map: dict[str, list[Path]] = defaultdict(list)
    native_ok = False
    for f in canon:
        uuid = info.get(str(f.resolve()), {}).get("BurstUUID")
        if uuid:
            uuid_map[str(uuid)].append(f)
            native_ok = True
    if native_ok:
        _assign_native(canon, uuid_map, info, result)
    else:
        _group_time_sim(canon, info, result, gap_s, min_gap, sim_thresh,
                        method="time_similarity")


def _assign_native(canon, uuid_map, info, result):
    """按 BurstUUID 分组：有 UUID 的按 UUID 聚组，无 UUID 的走时间+相似度。"""
    # 无 UUID 的佳能文件单独走时间+相似度
    no_uuid = [f for f in canon if not info.get(str(f.resolve()), {}).get("BurstUUID")]
    _group_time_sim(no_uuid, info, result, GAP_S, MIN_GAP, SIM_THRESH, method="time_similarity")

    # 有 UUID 的组，按组内时间排序给 seq
    next_gid = _next_group_id(result)
    for uuid, group in sorted(uuid_map.items(), key=lambda kv: _min_time(kv[1], info)):
        group = sorted(group, key=lambda f: _sortable(info, f))
        for i, f in enumerate(group, 1):
            result[str(f.resolve())] = (next_gid, i, "canon_native")
        next_gid += 1


def _group_time_sim(files, info, result, gap_s, min_gap, sim_thresh,
                    method="time_similarity"):
    """时间 + 相似度分组（所有品牌通用）。"""
    if not files:
        return
    files = sorted(files, key=lambda f: _sortable(info, f))
    next_gid = _next_group_id(result)
    gid = next_gid
    cur = [files[0]]

    for a, b in zip(files, files[1:]):
        ta = _sortable(info, a)
        tb = _sortable(info, b)
        gap = tb - ta if (ta is not None and tb is not None) else GAP_S + 1

        split = False
        if gap >= gap_s:
            split = True
        elif gap < min_gap:
            split = False
        else:
            # 暧昧区间：用图像相似度判定
            ha, hb = _dhash(a), _dhash(b)
            if ha is not None and hb is not None and _hamming(ha, hb) > sim_thresh:
                split = True

        if split:
            for i, f in enumerate(cur, 1):
                result[str(f.resolve())] = (gid, i, method)
            gid += 1
            cur = [b]
        else:
            cur.append(b)

    for i, f in enumerate(cur, 1):
        result[str(f.resolve())] = (gid, i, method)


# ---------- 内部工具 ----------

def _sortable(info, f) -> float:
    _, t = parse_capture_time(info.get(str(f.resolve()), {}))
    # 读不到时间的排到最前，用 0 占位
    return t if t is not None else 0.0


def _min_time(group, info) -> float:
    return min((_sortable(info, f) for f in group), default=0.0)


def _next_group_id(result) -> int:
    return max((g for g, _, _ in result.values()), default=0) + 1


if __name__ == "__main__":
    import sys
    fs = [Path(a) for a in sys.argv[1:]]
    if not fs:
        print("用法: python burst_group.py <文件...>")
        sys.exit(1)
    res = group_bursts(fs)
    by_gid = defaultdict(list)
    for k, (g, s, m) in res.items():
        by_gid[g].append((s, Path(k).name, m))
    for g in sorted(by_gid):
        items = sorted(by_gid[g])
        m = items[0][2]
        print(f"连拍组{g:03d} ({m})  共{len(items)}张:")
        for s, name, _ in items:
            print(f"    序号{s:02d} {name}")
