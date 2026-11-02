#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rename_engine.py — 模板化 EXIF 重命名引擎。

把「固定命名格式」改成「模板 + 预设」，支持随时切换命名风格，
并内置「复制重命名」保护原始 RAW（默认）与「原地重命名」（可选，不推荐）。

占位符（模板里用花括号引用）：
  {date}     拍摄日期        → 20261102
  {time}     拍摄时间        → 153020
  {datetime} 日期_时间       → 20261102_153020
  {bird}     鸟种中文名      → 灰背鸫
  {latin}    拉丁名/物种码   → Turdus hortulorum
  {loc}      拍摄地点        → 深圳南山（读不到则 UnknownLoc）
  {burst}    连拍组编号      → 001（三位补零）
  {seq}      组内序号        → 02（两位补零）
  {orig}     原文件名(去扩展) → DSC01234
  {model}    相机型号        → Canon EOS R8
  {ext}      扩展名(带点)    → .CR3

预设：
  A 简洁归档   {datetime}_{bird}_连拍组{burst}_序号{seq}{ext}
  B 上传图鉴   {bird}_{date}_{loc}_序号{seq}{ext}
  C 保留相机序号 {orig}_{bird}_{date}{ext}
  自定义      任意模板字符串

EXIF 缺失兜底：读不到时间 → UnknownTime；读不到地点 → UnknownLoc；
不会因为某个字段缺失而中断。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from toolpaths import no_window_flags, find_exiftool

# 兜底值（EXIF 缺失时不中断，用这些占位）
UNKNOWN_TIME = "UnknownTime"
UNKNOWN_LOC = "UnknownLoc"
UNKNOWN_BIRD = "未知鸟"
UNKNOWN_CAM = "UnknownCam"

PRESETS: dict[str, str] = {
    "简洁归档": "{datetime}_{bird}_连拍组{burst}_序号{seq}{ext}",
    "上传图鉴": "{bird}_{date}_{loc}_序号{num}{ext}",
    "保留相机序号": "{orig}_{bird}_{date}{ext}",
}

# 所有合法占位符（{ext} 若未出现在模板里会自动补在末尾）
PLACEHOLDERS = ("date", "time", "datetime", "bird", "latin",
                "loc", "burst", "seq", "num", "orig", "model", "ext")


# ---------- EXIF 读取 ----------

def read_exif(path: Path) -> dict:
    """用 exiftool 一次读出命名所需的全部字段，返回 dict（失败返回空 dict）。"""
    cmd = [
        find_exiftool() or "exiftool", "-j",
        "-DateTimeOriginal",
        "-SubSecTimeOriginal",
        "-CreateDate",
        "-GPSLatitude",
        "-GPSLongitude",
        "-Make",
        "-Model",
        "-LensModel",
        "-ISO",
        "-FNumber",
        "-ExposureTime",
        # 佳能原生连拍标签（尝试读取；读不到走时间+相似度）
        "-BurstUUID",
        "-SequenceImageNumber",
        "-SequenceFileNumber",
        "-SequenceNumber",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                           creationflags=no_window_flags())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return {}
    if r.returncode != 0 or not r.stdout.strip():
        return {}
    try:
        data = json.loads(r.stdout)
        return data[0] if data else {}
    except (json.JSONDecodeError, IndexError):
        return {}


def _norm_subsec(v) -> str:
    """SubSecTimeOriginal 可能是 int(42) / '42' / '00' / '0'，统一成两位秒小数。"""
    if v in (None, "", "-"):
        return "0"
    s = str(v).strip()
    if not s.isdigit():
        return "0"
    return s.rjust(2, "0")[:3]  # 取两位：'42'→'42'，'5'→'05'，'00'→'00'


def parse_capture_time(exif: dict) -> tuple[Optional[datetime], Optional[float]]:
    """返回 (datetime 对象, 带亚秒的排序值 float)。

    排序值 = 秒级时间戳 + 亚秒/100，用于连拍分组的精确排序。
    读不到时间返回 (None, None)。
    """
    raw = exif.get("DateTimeOriginal") or exif.get("CreateDate")
    if not raw:
        return None, None
    m = re.match(r"(\d{4}):(\d{2}):(\d{2})\s+(\d{2}):(\d{2}):(\d{2})", str(raw))
    if not m:
        return None, None
    y, mo, d, h, mi, s = (int(g) for g in m.groups())
    try:
        dt = datetime(y, mo, d, h, mi, s)
    except ValueError:
        return None, None
    subsec = _norm_subsec(exif.get("SubSecTimeOriginal"))
    sortable = float(dt.timestamp()) + float(subsec) / 100.0
    return dt, sortable


def format_datetime(exif: dict) -> Optional[str]:
    """YYYYMMDD_HHMMSS 格式（向后兼容旧调用方）。"""
    dt, _ = parse_capture_time(exif)
    return dt.strftime("%Y%m%d_%H%M%S") if dt else None


def format_date(exif: dict) -> str:
    dt, _ = parse_capture_time(exif)
    return dt.strftime("%Y%m%d") if dt else UNKNOWN_TIME


def format_time(exif: dict) -> str:
    dt, _ = parse_capture_time(exif)
    return dt.strftime("%H%M%S") if dt else UNKNOWN_TIME


# ---------- 模板渲染 ----------

def sanitize(name: str) -> str:
    """去掉文件系统非法字符，去掉首尾空格与点。"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip()
    name = name.strip(" .")
    return name or "unnamed"


def build_fields(exif: dict, *, bird: Optional[str] = None,
                 latin: Optional[str] = None, loc: Optional[str] = None,
                 burst: Optional[int] = None, seq: Optional[int] = None,
                 num: Optional[int] = None,
                 orig_name: Optional[str] = None) -> dict:
    """把 EXIF + 识别结果 + 连拍信息汇总成模板字段 dict（含兜底值）。"""
    p = Path(orig_name) if orig_name else None
    ext = p.suffix if p else ""
    return {
        "date": format_date(exif),
        "time": format_time(exif),
        "datetime": format_datetime(exif) or UNKNOWN_TIME,
        "bird": sanitize(bird) if bird else UNKNOWN_BIRD,
        "latin": sanitize(latin) if latin else "",
        "loc": sanitize(loc) if loc else UNKNOWN_LOC,
        "burst": f"{int(burst or 1):03d}",
        "seq": f"{int(seq or 1):02d}",
        "num": f"{int(num or 1):02d}",
        "orig": p.stem if p else "",
        "model": sanitize(f"{exif.get('Make', '')} {exif.get('Model', '')}".strip())
                 or UNKNOWN_CAM,
        "ext": ext,
    }


def render_name(template: str, fields: dict) -> str:
    """按模板渲染最终文件名。

    - 逐个替换占位符，未出现的占位符忽略。
    - {ext} 若模板里没写，自动补在末尾（保证扩展名不丢）。
    - 兜底：空 latin 会留空串，渲染后连续下划线会被压缩、首尾清理。
    """
    out = template
    for ph in PLACEHOLDERS:
        key = "{" + ph + "}"
        if key in out:
            out = out.replace(key, str(fields.get(ph, "")))

    if "{ext}" not in template:
        out += fields.get("ext", "")

    # 压掉 latin/其他字段为空时留下的连续下划线与空段
    out = re.sub(r"_+", "_", out)
    out = out.strip("_ ")
    return sanitize(out)


def validate_template(template: str) -> list[str]:
    """返回模板里出现的未知占位符（空列表 = 合法）。"""
    found = re.findall(r"\{(\w+)\}", template)
    return [f for f in found if f not in PLACEHOLDERS]


# ---------- 应用重命名（复制 / 原地） ----------

def apply_rename(src: Path, new_name: str, *, mode: str = "copy",
                 rename_dir: Optional[Path] = None,
                 dry_run: bool = False) -> Optional[Path]:
    """按新文件名落地。

    - mode='copy'（默认，保护原始 RAW）：把文件复制到 rename_dir（或源目录下
      renamed/ 子目录），原名不动。
    - mode='inplace'：原地重命名源文件（移动）。
    目标已存在时自动加 _1/_2 后缀，避免覆盖。
    """
    src = Path(src)
    if not src.exists():
        print(f"[SKIP] 源文件不存在: {src}", flush=True)
        return None

    new_name = sanitize(new_name)
    if not new_name.lower().endswith(src.suffix.lower()):
        new_name += src.suffix

    if mode == "copy":
        dst_dir = Path(rename_dir) if rename_dir else src.parent / "renamed"
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = _unique(dst_dir / new_name)
        if dry_run:
            print(f"[dry-run] 复制 {src.name} → {dst}", flush=True)
            return dst
        shutil.copy2(src, dst)
        print(f"[COPY] {src.name} → {dst.name}", flush=True)
        return dst

    # inplace
    dst = src.with_name(new_name)
    if dst == src:
        return src
    if dry_run:
        print(f"[dry-run] 重命名 {src.name} → {new_name}", flush=True)
        return dst
    dst = _unique(dst)
    src.rename(dst)
    print(f"[RENAME] {src.name} → {dst.name}", flush=True)
    return dst


def _unique(dst: Path) -> Path:
    if not dst.exists():
        return dst
    stem, ext = dst.stem, dst.suffix
    i = 1
    while True:
        cand = dst.with_name(f"{stem}_{i}{ext}")
        if not cand.exists():
            return cand
        i += 1


if __name__ == "__main__":
    # 自测：打印预设与示例渲染
    exif = {"DateTimeOriginal": "2026:11:02 15:30:20", "SubSecTimeOriginal": 45,
            "Make": "Canon", "Model": "EOS R8"}
    fields = build_fields(exif, bird="灰背鸫", latin="Turdus hortulorum",
                          loc="深圳南山", burst=1, seq=2, orig_name="DSC01234.CR3")
    for name, tpl in PRESETS.items():
        print(f"{name}: {render_name(tpl, fields)}")
    # 缺失兜底演示
    fields2 = build_fields({}, orig_name="IMG_0001.CR3")
    print("兜底(无EXIF):", render_name(PRESETS["简洁归档"], fields2))
