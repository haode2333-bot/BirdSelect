#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bird_pick.py — 扫描移动硬盘里的佳能 CR3，按鸟种分类，每个物种自动选 Top N 高质量片。

工作流：
  1. 递归扫描 --scan-dir 下所有图片（CR3 / JPG / PNG）
  2. 调 snipe-cli 本地模型做鸟体检测 + 物种识别（中文名，广东区域过滤）
  3. CR3 用 exiftool 提取内嵌全分辨率 JpgFromRaw；JPG/PNG 直接用原图
  4. 在鸟体 bbox 内算画质分（锐度 50% + 曝光 30% + 鸟体占比 20%）
  5. 按鸟种分组，每组按总分降序取 Top N，把对应 CR3 复制到输出目录
  6. 生成 bird_index.csv（精选索引）+ review_queue.csv（置信度 < 阈值待人工复核）
  7. 断点续扫：processed.json 记录每张文件的 size+mtime，重跑自动跳过未变更文件

依赖：
  pip install "snipe-cli[cpu]" pillow numpy opencv-python-headless tqdm
  系统需装 exiftool（Windows 下把 exiftool.exe 放 PATH；Mac: brew install exiftool）

原图不动：只复制 CR3 到输出目录，不修改、不移动原文件。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from toolpaths import find_exiftool, resolve_model_paths


# ---------- 工具函数 ----------

def sanitize_name(name: str) -> str:
    """Windows/macOS 文件系统安全的目录名。"""
    name = re.sub(r'[\\/:*?"<>|]', "_", name.strip())
    return name[:80] or "unknown"


def unique_dst(bird_dir: Path, filename: str) -> Path:
    """目标已存在时加 _1/_2 后缀，避免「只分类」模式下重名覆盖。"""
    dst = bird_dir / filename
    if not dst.exists():
        return dst
    stem = Path(filename).stem
    ext = Path(filename).suffix
    i = 1
    while True:
        dst = bird_dir / f"{stem}_{i}{ext}"
        if not dst.exists():
            return dst
        i += 1


# ---------- 超时保护（防卡死） ----------

SNIPE_TIMEOUT = 60.0       # 单张 snipe 识别超时（秒）
EXIFTOOL_TIMEOUT = 60.0    # exiftool 提取预览超时（秒）
_SNIPE_TIMEOUT = object()  # 识别超时的哨兵返回值


def exiftool_available() -> bool:
    return find_exiftool() is not None


def extract_preview(cr3_path: Path, out_dir: Path) -> Path | None:
    """从 RAW（CR3/NEF/ARW 等）提取内嵌全分辨率 JpgFromRaw 到 out_dir，返回 JPG 路径。
    尼康 NEF / 索尼 ARW 和佳能 CR3 一样都内嵌全尺寸 JpgFromRaw（约 2-5 MB），
    比 PreviewImage（仅几百 KB）清晰得多，适合做画质打分。
    """
    # JPG/PNG 等普通位图直接用原图打分，无需 exiftool 提取
    if cr3_path.suffix.lower() in (".jpg", ".jpeg", ".png"):
        return cr3_path

    jpg_path = out_dir / (cr3_path.stem + ".jpg")
    if jpg_path.exists() and jpg_path.stat().st_size > 20_000:
        return jpg_path

    # 先试 JpgFromRaw（全尺寸），fallback PreviewImage，都加超时防 exiftool 挂死。
    # 注意：exiftool 提取二进制 tag 必须用 stdout 重定向（-b 输出到 stdout），
    # 用 -o 会报 'Can't create JPEG files from other types' 导致提取失败。
    exif = find_exiftool()
    for tag in ("JpgFromRaw", "PreviewImage"):
        try:
            r = subprocess.run(
                [exif, "-b", f"-{tag}", str(cr3_path)],
                capture_output=True,
                timeout=EXIFTOOL_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            print(f"[TIMEOUT] exiftool 提取超时: {cr3_path.name}", flush=True)
            return None
        if r.returncode == 0 and len(r.stdout) > 20_000:
            try:
                jpg_path.write_bytes(r.stdout)
            except OSError:
                continue
            return jpg_path

    return None


# ---------- 画质打分 ----------

def quality_score(pil_img: Image.Image, bbox: tuple[int, int, int, int],
                  sharp_w: float = 0.5, exp_w: float = 0.3, area_w: float = 0.2) -> tuple[float, float, float]:
    """在鸟体 bbox 内算画质分。
    返回 (total, sharpness_norm, exposure_score)，全部 0-1。
    只看鸟本身，不看背景——这是生态摄影选片和普通选片的本质区别。
    """
    left, top, right, bottom = bbox
    left, top = max(0, left), max(0, top)
    right, bottom = min(pil_img.width, right), min(pil_img.height, bottom)
    if right - left < 10 or bottom - top < 10:
        return 0.0, 0.0, 0.0

    crop_gray = np.array(pil_img.crop((left, top, right, bottom)).convert("L"))

    # 1) 锐度：Laplacian 方差。生态摄影里焦点是生命线。
    lap_var = cv2.Laplacian(crop_gray, cv2.CV_64F).var()
    # 经验上鸟体清晰的片子 Laplacian 方差在 30-200 之间，归一化
    sharp_norm = min(lap_var / 80.0, 1.0)

    # 2) 曝光：直方图
    hist = cv2.calcHist([crop_gray], [0], None, [256], [0, 256]).flatten()
    total_pixels = crop_gray.size
    if total_pixels == 0:
        return 0.0, 0.0, 0.0
    over_ratio = hist[245:].sum() / total_pixels   # 过曝（高光死白）
    under_ratio = hist[:10].sum() / total_pixels   # 欠曝（暗部死黑）
    cumsum = np.cumsum(hist)
    median = int(np.searchsorted(cumsum, total_pixels * 0.5))
    dev = abs(median - 128) / 128.0                # 中位灰偏离 128 的程度
    exposure_score = max(0.0, 1.0 - dev - over_ratio * 3.0 - under_ratio * 3.0)

    # 3) 鸟体占画面比例
    bird_area = (right - left) * (bottom - top)
    img_area = pil_img.width * pil_img.height
    area_ratio = bird_area / img_area
    if area_ratio < 0.05:
        area_score = area_ratio / 0.05            # 太远
    elif area_ratio > 0.6:
        area_score = max(0.0, 1.0 - (area_ratio - 0.6) / 0.4)  # 贴边裁切
    else:
        area_score = 1.0

    # 加权：锐度权重最高（拍鸟的人都懂，眼实=片活）
    # 权重为相对比例，自动归一化到总和为 1，保证总分仍在 0-1
    w_sum = sharp_w + exp_w + area_w
    if w_sum <= 0:
        w_sum = 1.0
    total = (sharp_w * sharp_norm + exp_w * exposure_score + area_w * area_score) / w_sum
    # 转 Python 原生 float：hist 是 numpy.float32，直接 json.dumps 会报错
    return round(float(total), 4), round(float(sharp_norm), 4), round(float(exposure_score), 4)


def detect_with_timeout(img: Path, region: str, language: str = "CN",
                        timeout: float = SNIPE_TIMEOUT,
                        detector_model=None, classifier_model=None):
    """在 daemon 线程里跑 snipe 识别，超时返回 _SNIPE_TIMEOUT 哨兵。

    snipe 用 onnxruntime 推理会释放 GIL，线程方案可行；真卡死时该线程是
    daemon，进程退出会被强制回收。超时后跳过该张，不再永久卡住整批。
    """
    box: dict = {}

    def _run():
        try:
            from snipe import detect_birds  # 延迟导入，避免模块级依赖
            box["result"] = detect_birds(str(img), region_code=region,
                                         language_code=language,
                                         detector_model=detector_model,
                                         classifier_model=classifier_model)
        except Exception as e:  # noqa: BLE001
            box["error"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return _SNIPE_TIMEOUT
    if "error" in box:
        raise box["error"]
    return box.get("result")


# ---------- 主流程 ----------

def main():
    # 延迟导入重依赖，让 --help 在裸环境也能跑
    try:
        import cv2  # noqa: F401
        import numpy as np  # noqa: F401
        from PIL import Image  # noqa: F401
        from tqdm import tqdm
    except ImportError as e:
        sys.exit(f"缺少依赖: {e}\n请先: pip install pillow numpy opencv-python-headless tqdm")

    parser = argparse.ArgumentParser(
        description="扫描 RAW（CR3/NEF/ARW）与 JPG 鸟片，按物种自动选 Top N 高质量片",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--scan-dir", type=Path, required=True,
                        help="移动硬盘上鸟图根目录（递归扫描 *.CR3/*.NEF/*.ARW/*.JPG/*.PNG）")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="精选结果输出目录（会建在移动硬盘或内置盘均可）")
    parser.add_argument("--top-n", type=int, default=3,
                        help="每个物种保留几张最佳片")
    parser.add_argument("--conf-threshold", type=float, default=0.65,
                        help="物种识别置信度阈值，低于此值进 review_queue.csv")
    parser.add_argument("--sharp-weight", type=float, default=0.5,
                        help="画质评分中锐度的权重（相对比例，自动归一化）")
    parser.add_argument("--exposure-weight", type=float, default=0.3,
                        help="画质评分中曝光的权重（相对比例，自动归一化）")
    parser.add_argument("--area-weight", type=float, default=0.2,
                        help="画质评分中鸟体占比的权重（相对比例，自动归一化）")
    parser.add_argument("--region", default="CN-44",
                        help="省份代码用于物种白名单过滤（CN-44=广东，CN-11=北京）")
    parser.add_argument("--resume", action="store_true",
                        help="断点续扫：跳过 processed.json 里 size+mtime 未变的文件")
    parser.add_argument("--dry-run", action="store_true",
                        help="只识别打分，不复制 CR3 文件")
    parser.add_argument("--gui", action="store_true",
                        help="GUI 模式：每张输出 [PROGRESS] i/n 供桌面端解析进度条")
    parser.add_argument("--multi-bird", action="store_true",
                        help="一张图出现多种鸟时各归一类（默认只取置信度最高的一只）")
    parser.add_argument("--classify-all", action="store_true",
                        help="只分类不精选：所有识别出的照片都归档，不按 Top N 筛选")
    args = parser.parse_args()

    if not args.scan_dir.is_dir():
        sys.exit(f"扫描目录不存在: {args.scan_dir}")

    if not exiftool_available():
        sys.exit("找不到 exiftool。请先安装：\n"
                 "  macOS: brew install exiftool\n"
                 "  Windows: 下载 exiftool.exe 放 PATH 并改名 exiftool.exe")

    # 延迟 import snipe，方便 --help 在没装 snipe 时也能看
    try:
        from snipe import detect_birds
    except ImportError:
        sys.exit("未安装 snipe-cli。请先: pip install \"snipe-cli[cpu]\" && snipe init")

    # 用 GPU 加速识别（onnxruntime-gpu 需手动预加载 NVIDIA 运行库 DLL，否则静默回退 CPU）
    from gpu_preload import ensure_gpu
    ensure_gpu()

    # 解析模型路径：优先工具目录下 models/（便携版），否则 %APPDATA%/.snipe/models/
    detector_model, classifier_model = resolve_model_paths()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    picks_dir = args.output_dir / "picks"
    picks_dir.mkdir(exist_ok=True)
    tmp_dir = Path(tempfile.gettempdir()) / "bird_pick_previews"
    tmp_dir.mkdir(exist_ok=True)

    # 断点状态
    state_file = args.output_dir / "processed.json"
    processed: dict[str, dict] = {}
    if args.resume and state_file.exists():
        processed = json.loads(state_file.read_text(encoding="utf-8"))

    # 扫描所有图片（RAW 与 JPG/PNG，大小写都兼容：佳能 CR3 / 尼康 NEF / 索尼 ARW）
    exts = ("*.CR3", "*.cr3", "*.NEF", "*.nef", "*.ARW", "*.arw",
            "*.JPG", "*.jpg", "*.JPEG", "*.jpeg", "*.PNG", "*.png")
    image_files = []
    for ext in exts:
        image_files.extend(args.scan_dir.rglob(ext))
    image_files = sorted(set(image_files))
    print(f"找到 {len(image_files)} 个图片文件，区域过滤={args.region}，"
          f"阈值={args.conf_threshold}，每物种 Top{args.top_n}")

    records: list[dict] = []
    total = len(image_files)

    # GUI 模式用纯文本进度，CLI 模式用 tqdm 进度条
    pbar = None
    if not args.gui:
        pbar = tqdm(total=total, desc="扫描识别")

    def _log(msg: str):
        if pbar is not None:
            tqdm.write(msg)
        else:
            print(msg, flush=True)

    for idx, img in enumerate(image_files, 1):
        if args.gui:
            print(f"[PROGRESS] {idx}/{total}", flush=True)
        elif pbar is not None:
            pbar.update(1)

        key = str(img.resolve())
        st = img.stat()
        sig = f"{st.st_size}_{st.st_mtime_ns}"

        # 断点续扫：未变更的直接复用上次结果（新格式 value 含 recs 列表，兼容旧单条格式）
        if key in processed:
            _val = processed[key]
            if isinstance(_val, dict) and _val.get("sig") == sig:
                recs = _val.get("recs", []) if "recs" in _val else [_val]
                records.extend(recs)
                continue

        def _empty_rec():
            return {"path": key, "sig": sig, "bird": None,
                    "species_code": "", "detection_conf": 0.0,
                    "quality_total": 0.0, "quality_sharpness": 0.0,
                    "quality_exposure": 0.0, "bbox": None}

        try:
            detections = detect_with_timeout(img, args.region,
                                             detector_model=detector_model,
                                             classifier_model=classifier_model)
        except Exception as e:
            _log(f"[ERROR] 识别失败 {img.name}: {e}")
            rec = _empty_rec()
            processed[key] = {"sig": sig, "recs": [rec]}
            records.append(rec)
            continue

        if detections is _SNIPE_TIMEOUT:
            _log(f"[TIMEOUT] 识别超时跳过 {img.name}（>{SNIPE_TIMEOUT:.0f}s）")
            rec = _empty_rec()
            processed[key] = {"sig": sig, "recs": [rec]}
            records.append(rec)
            continue

        if not detections:
            rec = _empty_rec()
            processed[key] = {"sig": sig, "recs": [rec]}
            records.append(rec)
            continue

        # 决定记录哪些鸟：默认取置信度最高的一只；--multi-bird 时全部各归一类
        chosen = list(detections) if args.multi_bird else [max(detections, key=lambda d: d.score)]

        # 提取内嵌 JPG 做画质打分（一张图只提一次，供所有鸟共用）
        jpg_path = extract_preview(img, tmp_dir)

        recs = []
        for d in chosen:
            rec = {"path": key, "sig": sig,
                   "bird": d.bird_name,
                   "species_code": d.species_code,
                   "detection_conf": round(float(d.score), 3),
                   "quality_total": 0.0, "quality_sharpness": 0.0,
                   "quality_exposure": 0.0,
                   "bbox": [float(d.bbox.left), float(d.bbox.top),
                            float(d.bbox.right), float(d.bbox.bottom)]}
            if jpg_path is not None:
                try:
                    with Image.open(jpg_path) as pil:
                        pil.load()
                        # snipe 的 bbox 是基于它内部解码图的像素坐标
                        # 按比例映射到内嵌 JPG（两者分辨率应一致，这里仍做归一化保险）
                        sx = pil.width / d.image_width
                        sy = pil.height / d.image_height
                        mapped = (
                            int(d.bbox.left * sx), int(d.bbox.top * sy),
                            int(d.bbox.right * sx), int(d.bbox.bottom * sy),
                        )
                        q_total, q_sharp, q_exp = quality_score(
                            pil, mapped, args.sharp_weight, args.exposure_weight, args.area_weight)
                        rec["quality_total"] = q_total
                        rec["quality_sharpness"] = q_sharp
                        rec["quality_exposure"] = q_exp
                except Exception as e:
                    _log(f"[WARN] 画质打分失败 {img.name}: {e}")
            recs.append(rec)

        processed[key] = {"sig": sig, "recs": recs}
        records.extend(recs)

        # 每 10 张落一次盘，防止中途崩丢进度
        if len(records) % 10 == 0:
            state_file.write_text(
                json.dumps(processed, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    if pbar is not None:
        pbar.close()

    # 保存最终状态
    state_file.write_text(
        json.dumps(processed, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # ---------- 按物种分组取 Top N ----------
    by_bird: dict[str, list[dict]] = defaultdict(list)
    review: list[dict] = []
    no_bird_records: list[dict] = []

    for r in records:
        if not r.get("bird"):
            no_bird_records.append(r)
            continue
        if r["detection_conf"] < args.conf_threshold:
            review.append(r)
            continue
        by_bird[r["bird"]].append(r)

    # 统计要复制的文件总数（精选/分类 + 未识别 + 待复核去重后），用于复制进度
    total_copies = 0
    for _bird, _group in by_bird.items():
        total_copies += len(_group) if args.classify_all else min(len(_group), args.top_n)
    total_copies += len(no_bird_records)
    total_copies += len({r["path"] for r in review})

    copy_idx = 0

    def _tick_copy():
        nonlocal copy_idx
        copy_idx += 1
        if args.gui and total_copies > 0:
            print(f"[COPY] {copy_idx}/{total_copies}", flush=True)

    csv_rows: list[dict] = []
    for bird, group in sorted(by_bird.items()):
        group.sort(key=lambda r: r["quality_total"], reverse=True)
        if args.classify_all:
            selected = group            # 只分类：全部保留
        else:
            selected = group[: args.top_n]  # 精选：只取 Top N

        bird_dir = picks_dir / sanitize_name(bird)
        bird_dir.mkdir(exist_ok=True)

        for rank, r in enumerate(selected, 1):
            src = Path(r["path"])
            if args.classify_all:
                dst = unique_dst(bird_dir, src.name)   # 保留原名，重名加序号
            else:
                dst = bird_dir / f"top{rank}_{src.name}"
            if not args.dry_run and not dst.exists():
                shutil.copy2(src, dst)  # copy2 保留 mtime/权限
            _tick_copy()

            csv_rows.append({
                "rank": rank,
                "bird": bird,
                "species_code": r.get("species_code", ""),
                "source_cr3": str(src),
                "copied_to": str(dst),
                "detection_conf": r["detection_conf"],
                "quality_total": r["quality_total"],
                "quality_sharpness": r["quality_sharpness"],
                "quality_exposure": r["quality_exposure"],
            })

    # ---------- 未识别的图也复制到单独文件夹 ----------
    if no_bird_records:
        no_bird_dir = picks_dir / "未识别"
        no_bird_dir.mkdir(exist_ok=True)
        for r in no_bird_records:
            src = Path(r["path"])
            dst = no_bird_dir / src.name
            if not args.dry_run and not dst.exists():
                shutil.copy2(src, dst)
            _tick_copy()

    # ---------- 待人工复核的图复制到单独文件夹 ----------
    if review:
        review_dir = picks_dir / "待复核"
        review_dir.mkdir(exist_ok=True)
        seen = set()
        for r in sorted(review, key=lambda x: x["detection_conf"]):
            src = Path(r["path"])
            if str(src) in seen:  # 多鸟模式下同一张图可能有多条低置信度记录，去重
                continue
            seen.add(str(src))
            dst = unique_dst(review_dir, src.name)
            if not args.dry_run and not dst.exists():
                shutil.copy2(src, dst)
            _tick_copy()

    # ---------- 写 CSV ----------
    csv_path = args.output_dir / "bird_index.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        fields = ["rank", "bird", "species_code", "source_cr3", "copied_to",
                  "detection_conf", "quality_total", "quality_sharpness",
                  "quality_exposure"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(csv_rows)

    review_path = args.output_dir / "review_queue.csv"
    with review_path.open("w", newline="", encoding="utf-8-sig") as f:
        fields = ["path", "bird", "species_code", "detection_conf",
                  "quality_total"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in sorted(review, key=lambda x: x["detection_conf"]):
            w.writerow({k: r.get(k, "") for k in fields})

    # ---------- 人工修正记录（Hermes 读写这个 JSON） ----------
    corrections_file = args.output_dir / "corrections.json"
    if not corrections_file.exists():
        corrections_file.write_text("[]", encoding="utf-8")

    # ---------- 汇总 ----------
    print("\n" + "=" * 60)
    print(f"扫描完成: {len(image_files)} 张图片")
    print(f"  识别到鸟种: {len(by_bird)} 个")
    if args.classify_all:
        print(f"  分类输出:   {len(csv_rows)} 张（全部归档，不选 Top）")
    else:
        print(f"  精选输出:   {len(csv_rows)} 张（每物种 Top{args.top_n}）")
    print(f"  待人工复核: {len(review)} 张（置信度 < {args.conf_threshold}，已复制到 待复核/ 文件夹）")
    print(f"  未识别出鸟: {len(no_bird_records)} 张（已复制到 未识别/ 文件夹）")
    print(f"\n输出:")
    print(f"  精选目录: {picks_dir}")
    print(f"  索引表:   {csv_path}")
    print(f"  复核队列: {review_path}")
    print(f"  修正记录: {corrections_file}（你改一次鸟种名记这里，Hermes 下次读它）")
    if args.dry_run:
        print("\n[dry-run] 未复制任何文件")


if __name__ == "__main__":
    main()
#（注：内容由AI生成）
