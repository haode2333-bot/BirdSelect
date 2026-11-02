#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
exif_rename.py — EXIF 模板化重命名 CLI。

基于 rename_engine（模板/预设/复制/原地）+ burst_group（连拍分组）。

用法：
  # 单个文件（识别结果作为 --bird 传入）
  python exif_rename.py photo.CR3 --bird 灰背鸫 --preset 简洁归档 --dry-run

  # 批量：读 bird_index.csv，按鸟种分组 → 连拍分组 → 模板重命名
  python exif_rename.py --from-index bird_picks/bird_index.csv --preset 上传图鉴

  # 自定义模板 + 指定复制目标目录
  python exif_rename.py --from-index bird_index.csv --template "{bird}_{date}_{orig}{ext}" --rename-dir ./out

模式：
  --mode copy    复制重命名（默认，保护原始 RAW，原文件不动）
  --mode inplace 原地重命名（移动原文件，慎用）
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

from rename_engine import (PRESETS, PLACEHOLDERS, build_fields, read_exif,
                           render_name, apply_rename, validate_template)
from burst_group import group_bursts


def _rename_one(path: Path, bird: str | None, latin: str | None,
                template: str, mode: str, rename_dir: Path | None,
                loc: str | None, burst: int | None, seq: int | None,
                num: int | None, dry_run: bool) -> Path | None:
    exif = read_exif(path)
    fields = build_fields(exif, bird=bird, latin=latin, loc=loc,
                          burst=burst, seq=seq, num=num, orig_name=path.name)
    new_name = render_name(template, fields)
    return apply_rename(path, new_name, mode=mode, rename_dir=rename_dir,
                        dry_run=dry_run)


def rename_from_index(index_csv: Path, template: str, mode: str,
                      rename_dir: Path | None, loc: str | None,
                      dry_run: bool, no_burst: bool = False) -> int:
    """读 bird_index.csv，按鸟种 → 连拍分组 → 模板重命名。返回重命名文件数。"""
    if not index_csv.exists():
        sys.exit(f"索引文件不存在: {index_csv}")

    with index_csv.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    # 按鸟种聚合（copied_to 为实际落盘文件）
    by_bird: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        bird = (row.get("bird") or "").strip()
        copied = row.get("copied_to") or ""
        if not copied:
            continue
        key = bird or "未知鸟"
        by_bird[key].append(row)

    renamed = 0
    for bird, items in sorted(by_bird.items()):
        files = []
        for row in items:
            p = Path(row["copied_to"])
            if p.exists():
                files.append(p)
        if not files:
            continue
        # 连拍分组：优先用 CSV 已标注的 burst_group/burst_seq，否则现场计算
        have_burst_cols = bool(items) and all(
            ("burst_group" in row and "burst_seq" in row) for row in items)
        bursts = {}
        if not have_burst_cols and not no_burst:
            bursts = group_bursts(files)
        num = 0
        for row in items:
            p = Path(row["copied_to"])
            if not p.exists():
                continue
            if no_burst:
                g, s = 1, 1
            elif have_burst_cols:
                try:
                    g = int(row.get("burst_group") or 1)
                    s = int(row.get("burst_seq") or 1)
                except (TypeError, ValueError):
                    g, s = 1, 1
            else:
                g, s, _m = bursts.get(str(p.resolve()), (1, 1, "time_similarity"))
            num += 1
            result = _rename_one(p, bird if bird != "未知鸟" else None,
                                 row.get("species_code") or None,
                                 template, mode, rename_dir, loc, g, s, num, dry_run)
            if result is not None:
                renamed += 1

    print(f"\n处理完成，{renamed} 个文件 "
          f"{'（dry-run 未实际改动）' if dry_run else ('已复制' if mode == 'copy' else '已重命名')}")
    return renamed


def main():
    parser = argparse.ArgumentParser(
        description="EXIF 模板化重命名（复制/原地），支持连拍分组",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("file", nargs="?", type=Path, help="要重命名的单个文件")
    parser.add_argument("--bird", help="鸟种名（单个文件模式）")
    parser.add_argument("--from-index", type=Path, help="从 bird_index.csv 批量重命名")
    parser.add_argument("--preset", choices=list(PRESETS.keys()), help="命名预设")
    parser.add_argument("--template", help="自定义模板（覆盖 --preset），如 {bird}_{date}_{orig}{ext}")
    parser.add_argument("--mode", choices=["copy", "inplace"], default="copy",
                        help="copy=复制重命名(默认,不动原文件) / inplace=原地重命名")
    parser.add_argument("--rename-dir", type=Path, help="复制模式的目标目录（默认 源目录/renamed）")
    parser.add_argument("--location", help="拍摄地点（模板 {loc} 用，读不到 GPS 时兜底）")
    parser.add_argument("--no-burst", action="store_true",
                        help="关闭连拍分组（全部记为 组1/序1）")
    parser.add_argument("--dry-run", action="store_true", help="只打印不实际改名/复制")
    args = parser.parse_args()

    if args.template:
        template = args.template
        bad = validate_template(template)
        if bad:
            sys.exit(f"模板含未知占位符: {bad}。可用占位符: "
                     f"{', '.join('{'+p+'}' for p in PLACEHOLDERS)}")
    elif args.preset:
        template = PRESETS[args.preset]
    else:
        template = PRESETS["简洁归档"]

    if args.from_index:
        rename_from_index(args.from_index, template, args.mode,
                          args.rename_dir, args.location, args.dry_run,
                          args.no_burst)
        return

    if not args.file:
        sys.exit("请提供文件路径，或用 --from-index 指定 CSV")

    if not args.file.exists():
        sys.exit(f"文件不存在: {args.file}")

    exif = read_exif(args.file)
    print(f"文件: {args.file.name}")
    dt = exif.get("DateTimeOriginal") or exif.get("CreateDate")
    print(f"  拍摄时间: {dt or '未知'}")
    print(f"  相机: {exif.get('Make', '')} {exif.get('Model', '')}")
    fields = build_fields(exif, bird=args.bird, loc=args.location,
                          burst=1, seq=1, orig_name=args.file.name)
    print(f"  新文件名: {render_name(template, fields)}")
    result = _rename_one(args.file, args.bird, None, template, args.mode,
                         args.rename_dir, args.location, 1, 1, 1, args.dry_run)
    if result and not args.dry_run:
        print(f"\n结果: {result}")


if __name__ == "__main__":
    main()
