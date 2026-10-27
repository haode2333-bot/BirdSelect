#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
exif_rename.py — 读 EXIF 拍摄时间，结合鸟种名自动重命名。

命名规则：
  {鸟种名}_{YYYYMMDD_HHMM}_{原文件名}.CR3
  例：凤头鹰_20260815_1632_ABC0001.CR3

用法：
  # 单个文件（识别结果作为 --bird 参数传入）
  python exif_rename.py photo.CR3 --bird 凤头鹰

  # 批量：读 bird_picks/bird_index.csv，按索引重命名已复制的精选片
  python exif_rename.py --from-index bird_picks/bird_index.csv

  # dry-run 先看效果
  python exif_rename.py photo.CR3 --bird 凤头鹰 --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional


# ---------- EXIF 读取 ----------

def read_exif(path: Path) -> dict:
    """用 exiftool 读关键 EXIF 字段，返回 dict。"""
    # 一次取出所有需要的字段，JSON 输出便于解析
    cmd = [
        "exiftool", "-j",
        "-DateTimeOriginal",
        "-CreateDate",
        "-GPSLatitude",
        "-GPSLongitude",
        "-Make",
        "-Model",
        "-LensModel",
        "-ISO",
        "-FNumber",
        "-ExposureTime",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return {}
    if r.returncode != 0 or not r.stdout.strip():
        return {}
    try:
        data = json.loads(r.stdout)
        return data[0] if data else {}
    except (json.JSONDecodeError, IndexError):
        return {}


def format_datetime(exif: dict) -> Optional[str]:
    """从 EXIF 里取拍摄时间，格式化为 YYYYMMDD_HHMM。"""
    raw = exif.get("DateTimeOriginal") or exif.get("CreateDate")
    if not raw:
        return None
    # exiftool 格式: "2026:08:15 16:32:45"
    m = re.match(r"(\d{4}):(\d{2}):(\d{2})\s+(\d{2}):(\d{2})", str(raw))
    if not m:
        return None
    y, mo, d, h, mi = m.groups()
    return f"{y}{mo}{d}_{h}{mi}"


# ---------- 重命名 ----------

def build_new_name(bird_name: str, datetime_str: Optional[str], original_name: str) -> str:
    """构造新文件名，保留原扩展名。"""
    p = Path(original_name)
    stem = p.stem
    ext = p.suffix
    parts = []
    if bird_name:
        parts.append(sanitize(bird_name))
    if datetime_str:
        parts.append(datetime_str)
    parts.append(stem)
    return "_".join(parts) + ext


def sanitize(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name.strip())


def rename_file(path: Path, bird_name: str, dry_run: bool = False) -> Optional[Path]:
    """重命名单个文件，返回新路径（dry-run 也返回预期新路径）。"""
    exif = read_exif(path)
    dt = format_datetime(exif)
    new_name = build_new_name(bird_name, dt, path.name)
    new_path = path.with_name(new_name)

    if new_path == path:
        return path

    if dry_run:
        print(f"[dry-run] {path.name}  →  {new_name}")
        return new_path

    if new_path.exists():
        print(f"[SKIP] 目标已存在: {new_path.name}", file=sys.stderr)
        return None

    path.rename(new_path)
    print(f"[OK] {path.name}  →  {new_name}")
    return new_path


# ---------- 批量模式 ----------

def rename_from_index(index_csv: Path, dry_run: bool = False):
    """读 bird_index.csv，对里面 copied_to 列的文件重命名。"""
    if not index_csv.exists():
        sys.exit(f"索引文件不存在: {index_csv}")

    with index_csv.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    renamed = 0
    for row in rows:
        copied = row.get("copied_to", "")
        bird = row.get("bird", "")
        if not copied or not Path(copied).exists():
            continue
        result = rename_file(Path(copied), bird, dry_run=dry_run)
        if result is not None:
            renamed += 1

    print(f"\n处理完成，{renamed} 个文件 {'（dry-run 未实际改名）' if dry_run else '已重命名'}")


def main():
    parser = argparse.ArgumentParser(
        description="读 EXIF 拍摄时间，结合鸟种名重命名文件",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("file", nargs="?", type=Path, help="要重命名的单个文件")
    parser.add_argument("--bird", help="鸟种名（单个文件模式）")
    parser.add_argument("--from-index", type=Path, help="从 bird_index.csv 批量重命名")
    parser.add_argument("--dry-run", action="store_true", help="只打印不实际改名")
    args = parser.parse_args()

    if args.from_index:
        rename_from_index(args.from_index, dry_run=args.dry_run)
        return

    if not args.file:
        sys.exit("请提供文件路径，或用 --from-index 指定 CSV")

    if not args.file.exists():
        sys.exit(f"文件不存在: {args.file}")

    exif = read_exif(args.file)
    print(f"文件: {args.file.name}")
    print(f"  拍摄时间: {exif.get('DateTimeOriginal', '未知')}")
    print(f"  相机: {exif.get('Make', '')} {exif.get('Model', '')}")
    print(f"  镜头: {exif.get('LensModel', '未知')}")
    if exif.get("GPSLatitude"):
        print(f"  GPS: {exif.get('GPSLatitude')}, {exif.get('GPSLongitude')}")
    print(f"  ISO: {exif.get('ISO', '?')}  "
          f"光圈: f/{exif.get('FNumber', '?')}  "
          f"快门: {exif.get('ExposureTime', '?')}")

    result = rename_file(args.file, args.bird or "未识别", dry_run=args.dry_run)
    if result and not args.dry_run:
        print(f"\n新文件: {result}")


if __name__ == "__main__":
    main()
#（注：内容由AI生成）
