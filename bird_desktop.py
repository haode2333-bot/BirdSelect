#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bird_desktop.py — 观鸟影像本地处理桌面工具

纯本地运行，不依赖云端 API：
  - snipe-cli 本地鸟种识别
  - exiftool 提取 CR3 内嵌预览
  - 本地画质打分（锐度/曝光/占比）
  - 图鉴库查询 + 雌雄异形提示
  - 人工标注性别/亚成
  - EXIF 读取 + 自动重命名

启动：python bird_desktop.py
打包：pyinstaller --noconsole --onefile --add-data "data;data" bird_desktop.py
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageTk

# 同目录模块
from bird_guide import BirdGuide, get_guide
from rename_engine import (read_exif, format_datetime, build_fields, render_name,
                           PRESETS, apply_rename)
from toolpaths import (app_dir, find_exiftool, resolve_model_paths, no_window_flags,
                       extract_thumb_via_rawpy, is_raw)


# ---------- 全局配置 ----------

APP_DIR = app_dir()
DATA_DIR = APP_DIR / "data"
GALLERY_DIR = DATA_DIR / "gallery"
SETTINGS_FILE = APP_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "conf_threshold": 0.65,
    "top_n": 3,
    "region": "CN-44",
    "output_dir": "",
    "enable_sex_judge": True,   # 雌雄异形物种是否弹出人工标注
    "sharp_weight": 0.5,        # 画质评分：锐度权重
    "exposure_weight": 0.3,     # 画质评分：曝光权重
    "area_weight": 0.2,         # 画质评分：鸟体占比权重
    "multi_bird": False,        # 一张图多种鸟时是否各归一类
    "classify_all": False,      # 只分类不精选（不按 Top N 筛选）
    "use_gpu": True,            # 是否启用 GPU 加速（关闭则强制 CPU）
    # ---- EXIF 模板化重命名 ----
    "rename_preset": "简洁归档",  # 命名预设：简洁归档/上传图鉴/保留相机序号
    "rename_mode": "copy",       # copy=复制重命名(默认,保护原RAW) / inplace=原地重命名
    "rename_dir": "",            # 复制模式的目标目录（空=源目录下 renamed/）
    "location": "",              # 拍摄地点（模板 {loc} 用；留空读 GPS，读不到→UnknownLoc）
    "custom_template": "",       # 自定义模板（非空时覆盖预设）
    "burst_detect": True,        # 连拍识别（佳能原生标签 / 其余品牌时间+相似度）
}

REGIONS = {
    "CN-11": "北京",
    "CN-12": "天津",
    "CN-13": "河北",
    "CN-14": "山西",
    "CN-15": "内蒙古",
    "CN-21": "辽宁",
    "CN-22": "吉林",
    "CN-23": "黑龙江",
    "CN-31": "上海",
    "CN-32": "江苏",
    "CN-33": "浙江",
    "CN-34": "安徽",
    "CN-35": "福建",
    "CN-36": "江西",
    "CN-37": "山东",
    "CN-41": "河南",
    "CN-42": "湖北",
    "CN-43": "湖南",
    "CN-44": "广东",
    "CN-45": "广西",
    "CN-46": "海南",
    "CN-50": "重庆",
    "CN-51": "四川",
    "CN-52": "贵州",
    "CN-53": "云南",
    "CN-54": "西藏",
    "CN-61": "陕西",
    "CN-62": "甘肃",
    "CN-63": "青海",
    "CN-64": "宁夏",
    "CN-65": "新疆",
}


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return {**DEFAULT_SETTINGS, **saved}
    return dict(DEFAULT_SETTINGS)


def save_settings(s: dict):
    SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- 工具函数 ----------

def extract_cr3_preview(cr3_path: Path, out_dir: Path) -> Path | None:
    """从 RAW（CR3/NEF/ARW）提取内嵌全分辨率 JPG 用于显示和识别。

    注意：exiftool 提取二进制 tag 必须用 stdout 重定向（-b 输出到 stdout），
    用 -o 会报 'Can't create JPEG files from other types' 导致提取失败。
    """
    # 缓存键带 路径+大小+mtime 哈希：相机文件名会跨会话重复（都叫 IMG_XXXX），
    # 只用 stem 会把上一次的旧预览误当成当前文件的预览。
    try:
        st = cr3_path.stat()
        cache_key = hashlib.md5(
            f"{cr3_path.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode("utf-8")
        ).hexdigest()[:12]
    except OSError:
        cache_key = "nocache"
    jpg_path = out_dir / f"{cr3_path.stem}_{cache_key}_preview.jpg"
    if jpg_path.exists() and jpg_path.stat().st_size > 20_000:
        return jpg_path
    exif = find_exiftool()
    for tag in ("JpgFromRaw", "PreviewImage"):
        try:
            r = subprocess.run(
                [exif, "-b", f"-{tag}", str(cr3_path)],
                capture_output=True,
                timeout=60,
                creationflags=no_window_flags(),
            )
        except subprocess.TimeoutExpired:
            break
        if r.returncode == 0 and len(r.stdout) > 20_000:
            try:
                jpg_path.write_bytes(r.stdout)
            except OSError:
                continue
            return jpg_path

    # 跨品牌兜底：exiftool 取不到时用 rawpy（libraw），富士/奥林巴斯等也适用
    thumb = extract_thumb_via_rawpy(cr3_path)
    if thumb and len(thumb) > 5_000:
        try:
            jpg_path.write_bytes(thumb)
        except OSError:
            pass
    return jpg_path if jpg_path.exists() else None


def load_ctk_image(path: Path, max_w: int, max_h: int) -> ctk.CTkImage | None:
    """加载图片并缩放到指定尺寸，返回 CTkImage。"""
    try:
        img = Image.open(path)
        img.load()
        # 等比缩放
        w, h = img.size
        ratio = min(max_w / w, max_h / h)
        new_w, new_h = int(w * ratio), int(h * ratio)
        return ctk.CTkImage(light_image=img, dark_image=img, size=(new_w, new_h))
    except Exception:
        return None


# ---------- 主窗口 ----------

class _LineStream:
    """按行转发 stdout（冻结模式下用 redirect_stdout 捕获 bird_pick 的进度输出）。"""

    def __init__(self, on_line):
        self._buf = ""
        self._on_line = on_line

    def write(self, s):
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._on_line(line.rstrip("\r"))
        return len(s)

    def flush(self):
        if self._buf:
            self._on_line(self._buf.rstrip("\r"))
            self._buf = ""


class BirdApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.guide: BirdGuide = get_guide()

        self.title("观鸟影像处理工具")
        self._apply_window_geometry()
        self.minsize(900, 600)

        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        # 当前选中的单张识别文件
        self.current_image_path: Path | None = None
        self.current_preview_path: Path | None = None
        self.current_result: dict | None = None

        # 批量扫描状态
        self.proc: subprocess.Popen | None = None
        self.batch_running = False
        self._cancelled = False
        self._eta_sec: int | None = None

        self._build_layout()
        self._show_batch()  # 默认显示批量页

    def _apply_window_geometry(self):
        """窗口按屏幕工作区（排除任务栏/菜单栏）自适应，避免底部按钮被挡。"""
        try:
            if os.name == "nt":
                import ctypes

                class RECT(ctypes.Structure):
                    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

                rect = RECT()
                # SPI_GETWORKAREA = 0x0030：屏幕工作区（不含任务栏）
                ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
                wa_w = rect.right - rect.left
                wa_h = rect.bottom - rect.top
                w = max(900, int(wa_w * 0.8))
                h = max(600, int(wa_h * 0.8))
                x = rect.left + (wa_w - w) // 2
                y = rect.top + (wa_h - h) // 2
            else:
                # macOS / Linux：用 tk 的屏幕尺寸，无 ctypes.windll
                sw = self.winfo_screenwidth()
                sh = self.winfo_screenheight()
                w = max(900, int(sw * 0.8))
                h = max(600, int(sh * 0.8))
                x = (sw - w) // 2
                y = (sh - h) // 2
            self.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            self.geometry("1200x800")

    def _build_layout(self):
        # 左侧导航
        self.nav = ctk.CTkFrame(self, width=180, corner_radius=0)
        self.nav.pack(side="left", fill="y")
        self.nav.pack_propagate(False)

        ctk.CTkLabel(self.nav, text="🐦 观鸟工具", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(20, 20))

        self.btn_batch = ctk.CTkButton(self.nav, text="批量扫描", command=self._show_batch)
        self.btn_batch.pack(pady=5, padx=10, fill="x")

        self.btn_single = ctk.CTkButton(self.nav, text="单张识别", command=self._show_single)
        self.btn_single.pack(pady=5, padx=10, fill="x")

        self.btn_settings = ctk.CTkButton(self.nav, text="设置", command=self._show_settings)
        self.btn_settings.pack(pady=5, padx=10, fill="x")

        # 底部版本信息
        ctk.CTkLabel(self.nav, text="纯本地运行", font=ctk.CTkFont(size=11), text_color="gray").pack(side="bottom", pady=10)

        # 右侧内容区
        self.content = ctk.CTkFrame(self, corner_radius=0)
        self.content.pack(side="right", fill="both", expand=True)

    def _clear_content(self):
        for w in self.content.winfo_children():
            w.destroy()

    # ---------- 批量扫描页 ----------

    def _show_batch(self):
        self._clear_content()
        self._highlight_nav(self.btn_batch)

        frame = ctk.CTkFrame(self.content)
        frame.pack(fill="both", expand=True, padx=20, pady=20)

        ctk.CTkLabel(frame, text="批量扫描硬盘 · 每物种选 Top3", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(15, 10))

        # 扫描目录
        row1 = ctk.CTkFrame(frame, fg_color="transparent")
        row1.pack(fill="x", padx=20, pady=5)
        ctk.CTkLabel(row1, text="扫描目录:", width=100).pack(side="left")
        self.batch_scan_dir = ctk.CTkEntry(row1, placeholder_text="选择移动硬盘上的鸟图文件夹")
        self.batch_scan_dir.pack(side="left", fill="x", expand=True, padx=5)
        ctk.CTkButton(row1, text="浏览", width=60, command=self._pick_scan_dir).pack(side="left")

        # 输出目录
        row2 = ctk.CTkFrame(frame, fg_color="transparent")
        row2.pack(fill="x", padx=20, pady=5)
        ctk.CTkLabel(row2, text="输出目录:", width=100).pack(side="left")
        self.batch_out_dir = ctk.CTkEntry(row2, placeholder_text="精选结果输出位置")
        self.batch_out_dir.pack(side="left", fill="x", expand=True, padx=5)
        ctk.CTkButton(row2, text="浏览", width=60, command=self._pick_out_dir).pack(side="left")
        if self.settings.get("output_dir"):
            self.batch_out_dir.insert(0, self.settings["output_dir"])

        # 参数显示
        info = f"置信阈值: {self.settings['conf_threshold']}  |  每物种 Top{self.settings['top_n']}  |  地区: {REGIONS.get(self.settings['region'], self.settings['region'])}"
        ctk.CTkLabel(frame, text=info, text_color="gray", font=ctk.CTkFont(size=12)).pack(pady=5)

        # 开始 / 取消按钮
        btn_row = ctk.CTkFrame(frame, fg_color="transparent")
        btn_row.pack(pady=15)
        self.btn_start = ctk.CTkButton(btn_row, text="开始扫描", command=self._start_batch,
                                       font=ctk.CTkFont(size=14, weight="bold"),
                                       height=40, width=160)
        self.btn_start.pack(side="left", padx=5)
        self.btn_cancel = ctk.CTkButton(btn_row, text="取消扫描", command=self._cancel_batch,
                                        height=40, width=160, state="disabled",
                                        fg_color="#c0392b", hover_color="#a93226")
        self.btn_cancel.pack(side="left", padx=5)

        # 进度条 + 进度文字
        prog_row = ctk.CTkFrame(frame, fg_color="transparent")
        prog_row.pack(fill="x", padx=20, pady=5)
        self.progress = ctk.CTkProgressBar(prog_row)
        self.progress.pack(side="left", fill="x", expand=True)
        self.progress.set(0)
        self.progress_label = ctk.CTkLabel(prog_row, text="0 / 0", width=130, anchor="e")
        self.progress_label.pack(side="left", padx=(10, 0))

        # 日志
        ctk.CTkLabel(frame, text="运行日志:", anchor="w").pack(fill="x", padx=20, pady=(10, 0))
        self.batch_log = ctk.CTkTextbox(frame, height=250)
        self.batch_log.pack(fill="both", expand=True, padx=20, pady=(0, 15))

    def _pick_scan_dir(self):
        d = filedialog.askdirectory(title="选择鸟图目录")
        if d:
            self.batch_scan_dir.delete(0, "end")
            self.batch_scan_dir.insert(0, d)

    def _pick_out_dir(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.batch_out_dir.delete(0, "end")
            self.batch_out_dir.insert(0, d)

    def _pick_rename_dir(self):
        d = filedialog.askdirectory(title="选择复制目标目录")
        if d:
            self.rename_dir_entry.delete(0, "end")
            self.rename_dir_entry.insert(0, d)

    def _start_batch(self):
        scan_dir = self.batch_scan_dir.get().strip()
        out_dir = self.batch_out_dir.get().strip()
        if not scan_dir or not Path(scan_dir).is_dir():
            messagebox.showerror("错误", "请选择有效的扫描目录")
            return
        if not out_dir:
            messagebox.showerror("错误", "请选择输出目录")
            return

        self.btn_start.configure(state="disabled", text="扫描中…")
        self.btn_cancel.configure(state="normal")
        self._set_nav_enabled(False)  # 扫描中锁定导航，防止切换页面中断选片
        self.batch_running = True
        self._cancelled = False
        self._eta_sec = None
        self.batch_log.delete("1.0", "end")
        self.progress.set(0)
        self.progress_label.configure(text="0 / 0")
        self._log(f"开始扫描: {scan_dir}")
        self._log(f"输出到: {out_dir}")

        # 后台线程跑
        t = threading.Thread(target=self._run_batch, args=(scan_dir, out_dir), daemon=True)
        t.start()

    def _run_batch(self, scan_dir: str, out_dir: str):
        # 拼好 bird_pick 的 CLI 参数（subprocess 和冻结进程内两种方式共用）
        argv = [
            "--scan-dir", scan_dir,
            "--output-dir", out_dir,
            "--top-n", str(self.settings["top_n"]),
            "--conf-threshold", str(self.settings["conf_threshold"]),
            "--region", self.settings["region"],
            "--sharp-weight", str(self.settings["sharp_weight"]),
            "--exposure-weight", str(self.settings["exposure_weight"]),
            "--area-weight", str(self.settings["area_weight"]),
            "--resume",
            "--gui",
        ]
        if self.settings.get("multi_bird"):
            argv.append("--multi-bird")
        if self.settings.get("classify_all"):
            argv.append("--classify-all")
        if not self.settings.get("use_gpu", True):
            argv.append("--cpu")
        if not self.settings.get("burst_detect", True):
            argv.append("--no-burst-detect")

        def handle_line(line: str):
            line = line.rstrip()
            if not line:
                return
            m = re.match(r"\[PROGRESS\]\s+(\d+)/(\d+)", line)
            if m:
                cur, tot = int(m.group(1)), int(m.group(2))
                self.after(0, lambda c=cur, t=tot: self._update_progress(c, t, "识别"))
                return
            m2 = re.match(r"\[COPY\]\s+(\d+)/(\d+)", line)
            if m2:
                cur, tot = int(m2.group(1)), int(m2.group(2))
                self.after(0, lambda c=cur, t=tot: self._update_progress(c, t, "复制"))
                return
            m3 = re.match(r"\[BIRD\]\s+(.*)", line)
            if m3:
                self._log(m3.group(1))  # 鸟种概要行：去掉前缀直接显示
                return
            m4 = re.match(r"\[ETA\]\s+(\d+)", line)
            if m4:
                sec = int(m4.group(1))
                self.after(0, lambda s=sec: self._set_eta(s))
                return
            m5 = re.match(r"\[START\]\s+total=(\d+)\s+backend=(\w+)\s+est=(\d+)", line)
            if m5:
                tot, backend, est = int(m5.group(1)), m5.group(2), int(m5.group(3))
                self.after(0, lambda: self._log(
                    f"识别后端: {backend}（{'GPU 加速' if backend == 'CUDA' else 'CPU 模式'}）· "
                    f"共 {tot} 张 · 预计约 {self._format_secs(est)}"))
                return
            self._log(line)

        try:
            if getattr(sys, "frozen", False):
                # 冻结版（exe）：bird_pick 已打进 exe，必须进程内调用。
                # 用 sys.executable + bird_pick.py 会再启动一个 GUI（就是「弹另一个界面」的原因）。
                self._log(f"执行: BirdSelect {' '.join(argv)}")
                import bird_pick
                import contextlib
                stream = _LineStream(handle_line)
                old_argv = sys.argv
                sys.argv = ["bird_pick"] + argv
                try:
                    with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                        bird_pick.main()
                except SystemExit:
                    pass
                finally:
                    sys.argv = old_argv
                self._log("\n完成")
                self.after(0, lambda: messagebox.showinfo("完成", f"扫描完成，结果在:\n{out_dir}"))
            else:
                # 开发版/便携版：subprocess 调 bird_pick.py（最稳，能独立取消）
                cmd = [sys.executable, str(APP_DIR / "bird_pick.py")] + argv
                self._log(f"执行: {' '.join(cmd)}")
                env = dict(os.environ)
                env["PYTHONUTF8"] = "1"
                env["PYTHONIOENCODING"] = "utf-8"
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                             text=True, encoding="utf-8", errors="replace", env=env,
                                             creationflags=no_window_flags())
                for line in self.proc.stdout:
                    handle_line(line)
                self.proc.wait()
                code = self.proc.returncode
                if code == 0:
                    self._log("\n完成，退出码 0")
                    self.after(0, lambda: messagebox.showinfo("完成", f"扫描完成，结果在:\n{out_dir}"))
                elif getattr(self, "_cancelled", False):
                    self._log("\n已取消扫描。")
                else:
                    self._log(f"\n扫描异常结束，退出码 {code}")
                    self.after(0, lambda: messagebox.showerror("错误", f"扫描异常结束，退出码 {code}"))
        except Exception as e:
            self._log(f"错误: {e}")
            self.after(0, lambda: messagebox.showerror("错误", str(e)))
        finally:
            self.proc = None
            self.batch_running = False
            self.after(0, self._reset_batch_ui)

    def _log(self, msg: str):
        self.batch_log.insert("end", msg + "\n")
        self.batch_log.see("end")
        self.update_idletasks()

    def _update_progress(self, cur: int, tot: int, phase: str = "识别"):
        if tot > 0:
            self.progress.set(cur / tot)
        text = f"{phase} {cur} / {tot}"
        if getattr(self, "_eta_sec", None):
            text += f" · 剩余约 {self._format_secs(self._eta_sec)}"
        self.progress_label.configure(text=text)

    def _set_eta(self, seconds: int):
        self._eta_sec = seconds

    @staticmethod
    def _format_secs(seconds: int) -> str:
        seconds = int(seconds)
        if seconds >= 3600:
            return f"{seconds // 3600}时{(seconds % 3600) // 60}分"
        if seconds >= 60:
            return f"{seconds // 60}分{seconds % 60}秒"
        return f"{seconds}秒"

    def _set_nav_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for btn in (self.btn_batch, self.btn_single, self.btn_settings):
            btn.configure(state=state)

    def _reset_batch_ui(self):
        self.btn_start.configure(state="normal", text="开始扫描")
        self.btn_cancel.configure(state="disabled")
        self._set_nav_enabled(True)

    def _cancel_batch(self):
        self._cancelled = True
        if self.proc and self.proc.poll() is None:
            self._log("\n[取消] 正在终止扫描进程…")
            try:
                self.proc.kill()
                self._log("[取消] 已终止。可用断点续扫（--resume）下次从已处理处继续。")
            except Exception as e:
                self._log(f"[取消] 终止失败: {e}")
        self._reset_batch_ui()

    # ---------- 单张识别页 ----------

    def _show_single(self):
        self._clear_content()
        self._highlight_nav(self.btn_single)

        outer = ctk.CTkFrame(self.content)
        outer.pack(fill="both", expand=True, padx=20, pady=20)

        # 底部按钮行：先 pack 固定底部，窗口缩小时不会被任务栏/下边缘裁掉
        btn_row = ctk.CTkFrame(outer, fg_color="transparent")
        btn_row.pack(side="bottom", fill="x", pady=(5, 0))
        self.btn_identify = ctk.CTkButton(btn_row, text="🔍 开始识别", command=self._identify_single,
                                          height=36, width=150)
        self.btn_identify.pack(side="left", padx=5)
        _rename_btn_text = ("✏️ 复制重命名" if self.settings.get("rename_mode", "copy") == "copy"
                            else "✏️ 原地重命名")
        self.btn_rename = ctk.CTkButton(btn_row, text=_rename_btn_text, command=self._do_rename,
                                        height=36, width=150, state="disabled")
        self.btn_rename.pack(side="left", padx=5)

        # 结果区（识别结果 + 性别标注 + 重命名预览），紧贴底部按钮之上
        result_frame = ctk.CTkFrame(outer)
        result_frame.pack(side="bottom", fill="x", padx=10, pady=5)

        self.result_label = ctk.CTkLabel(result_frame, text="", font=ctk.CTkFont(size=14),
                                         justify="left", anchor="w")
        self.result_label.pack(fill="x", padx=15, pady=(10, 5))

        # 性别标注
        sex_row = ctk.CTkFrame(result_frame, fg_color="transparent")
        sex_row.pack(fill="x", padx=15, pady=2)
        ctk.CTkLabel(sex_row, text="性别/阶段标注:").pack(side="left")
        self.sex_var = ctk.StringVar(value="不标注")
        self.sex_menu = ctk.CTkOptionMenu(sex_row, variable=self.sex_var,
                                          values=["不标注", "成雄", "成雌", "亚成", "幼鸟"])
        self.sex_menu.pack(side="left", padx=10)

        # 重命名预览
        rename_row = ctk.CTkFrame(result_frame, fg_color="transparent")
        rename_row.pack(fill="x", padx=15, pady=(2, 10))
        ctk.CTkLabel(rename_row, text="重命名预览:").pack(side="left")
        self.rename_label = ctk.CTkLabel(rename_row, text="", text_color="gray", anchor="w")
        self.rename_label.pack(side="left", padx=10)

        # 顶部：标题 + 选文件按钮
        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(side="top", fill="x")
        ctk.CTkLabel(header, text="单张识别 · 图鉴对比 · 人工标注",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(5, 5))
        ctk.CTkButton(header, text="选择鸟片 (CR3/NEF/ARW/JPG)", command=self._pick_single_image,
                      height=36).pack(pady=5)

        # 中部：左右对比区（可伸缩，空间不足时优先压缩这里）
        compare = ctk.CTkFrame(outer, fg_color="transparent")
        compare.pack(fill="both", expand=True, padx=10, pady=10)

        # 左：你的照片
        left = ctk.CTkFrame(compare)
        left.pack(side="left", fill="both", expand=True, padx=5)
        ctk.CTkLabel(left, text="你的照片", font=ctk.CTkFont(weight="bold")).pack(pady=5)
        self.single_img_label = ctk.CTkLabel(left, text="（未选择图片）", width=400, height=300)
        self.single_img_label.pack(pady=10)

        # 右：图鉴
        right = ctk.CTkFrame(compare)
        right.pack(side="right", fill="both", expand=True, padx=5)
        ctk.CTkLabel(right, text="图鉴参考", font=ctk.CTkFont(weight="bold")).pack(pady=5)
        self.gallery_img_label = ctk.CTkLabel(right, text="（识别后显示）", width=400, height=300)
        self.gallery_img_label.pack(pady=10)

        # 性别标注变化时更新重命名预览
        self.sex_var.trace_add("write", lambda *_: self._update_rename_preview())

    def _pick_single_image(self):
        path = filedialog.askopenfilename(
            title="选择鸟片",
            filetypes=[("鸟片 (RAW/JPG)",
                        "*.CR3 *.cr3 *.CR2 *.cr2 *.NEF *.nef *.NRW *.nrw "
                        "*.ARW *.arw *.SR2 *.sr2 *.RAF *.raf *.RW2 *.rw2 "
                        "*.ORF *.orf *.PEF *.pef *.DNG *.dng *.RWL *.rwl "
                        "*.jpg *.jpeg *.png *.JPG *.JPEG *.PNG"),
                       ("所有文件", "*.*")]
        )
        if not path:
            return
        self.current_image_path = Path(path)
        self.current_result = None

        # 显示图片
        preview_dir = APP_DIR / ".tmp_preview"
        preview_dir.mkdir(exist_ok=True)

        if is_raw(self.current_image_path):
            self.single_img_label.configure(text="正在提取 RAW 预览…")
            self.update()
            prev = extract_cr3_preview(self.current_image_path, preview_dir)
            self.current_preview_path = prev
        else:
            self.current_preview_path = self.current_image_path

        if self.current_preview_path:
            img_ctk = load_ctk_image(self.current_preview_path, 450, 300)
            if img_ctk:
                self.single_img_label.configure(image=img_ctk, text="")
                self.single_img_label.image = img_ctk
            else:
                self.single_img_label.configure(text="图片加载失败", image=None)
        else:
            self.single_img_label.configure(text="无法提取预览", image=None)

    def _identify_single(self):
        if not self.current_image_path:
            messagebox.showwarning("提示", "请先选择图片")
            return

        self.btn_identify.configure(state="disabled", text="识别中…")
        self.update()

        def worker():
            try:
                from snipe import detect_birds
                # 用 GPU 加速识别（onnxruntime-gpu 需手动预加载 NVIDIA 运行库 DLL，否则静默回退 CPU）
                from gpu_preload import ensure_gpu
                ensure_gpu(force_cpu=not self.settings.get("use_gpu", True))
                # 用提取出的预览 JPG 识别（比直接读 RAW 快）
                target = self.current_preview_path or self.current_image_path
                detector_model, classifier_model = resolve_model_paths()
                detections = detect_birds(
                    str(target),
                    region_code=self.settings["region"],
                    language_code="CN",
                    detector_model=detector_model,
                    classifier_model=classifier_model,
                )

                if not detections:
                    self.current_result = {"bird": None}
                    self.after(0, lambda: self._show_result({"bird": None}))
                    return

                best = max(detections, key=lambda d: d.score)
                entry = self.guide.lookup(best.bird_name)

                # 读 EXIF
                exif = read_exif(self.current_image_path)
                dt = format_datetime(exif)

                result = {
                    "bird": best.bird_name,
                    "latin": best.species_code,
                    "conf": float(best.score),
                    "bbox": [best.bbox.left, best.bbox.top, best.bbox.right, best.bbox.bottom],
                    "guide": entry,
                    "exif": exif,
                    "datetime": dt,
                }
                self.current_result = result
                self.after(0, lambda: self._show_result(result))
            except ImportError:
                self.after(0, lambda: messagebox.showerror("错误",
                    "未安装 snipe-cli。请先: pip install \"snipe-cli[cpu]\" && snipe init"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("识别失败", str(e)))
            finally:
                self.after(0, lambda: self.btn_identify.configure(state="normal", text="🔍 开始识别"))

        threading.Thread(target=worker, daemon=True).start()

    def _show_result(self, result: dict):
        if not result.get("bird"):
            self.result_label.configure(text="未识别出鸟类。", text_color="gray")
            self.btn_rename.configure(state="disabled")
            return

        bird = result["bird"]
        conf = result["conf"]
        entry = result.get("guide")
        exif = result.get("exif", {})

        lines = [f"✓ 物种: {bird}  ({result['latin']})  置信度 {conf:.1%}"]

        if conf < self.settings["conf_threshold"]:
            lines.append("⚠️ 置信度低于阈值，请人工复核")
        else:
            lines.append("✓ 置信度达标")

        if entry:
            lines.append(f"分类: {entry.order} > {entry.family} > {entry.genus}")
            if entry.sexually_dimorphic:
                lines.append("⚠️ 此物种雌雄异形，请在下方标注性别/阶段")
            else:
                lines.append("雌雄同色（体型可能有差异）")
            lines.append(f"羽色: {entry.plumage_notes}")

            # 加载图鉴图
            if entry.gallery_path and entry.gallery_path.exists():
                g_img = load_ctk_image(entry.gallery_path, 450, 300)
                if g_img:
                    self.gallery_img_label.configure(image=g_img, text="")
                    self.gallery_img_label.image = g_img
            else:
                self.gallery_img_label.configure(text="（暂无图鉴图\n把图放 data/gallery/）", image=None)
        else:
            lines.append("（此物种暂未收录在本地图鉴库）")

        if exif:
            lines.append(f"拍摄: {exif.get('DateTimeOriginal', '未知')}  "
                         f"{exif.get('Model', '')}  f/{exif.get('FNumber', '?')}  "
                         f"ISO{exif.get('ISO', '?')}")

        self.result_label.configure(text="\n".join(lines), text_color="#1a1a1a")
        self.btn_rename.configure(state="normal")
        self._update_rename_preview()

    def _current_template(self) -> str:
        custom = (self.settings.get("custom_template") or "").strip()
        if custom:
            return custom
        return PRESETS.get(self.settings.get("rename_preset"), PRESETS["简洁归档"])

    def _update_rename_preview(self):
        if not self.current_result or not self.current_result.get("bird"):
            self.rename_label.configure(text="")
            return
        bird = self.current_result["bird"]
        sex = self.sex_var.get()
        if sex and sex != "不标注":
            bird = f"{bird}_{sex}"
        exif = self.current_result.get("exif", {})
        fields = build_fields(exif, bird=bird,
                              latin=self.current_result.get("latin"),
                              loc=self.settings.get("location") or None,
                              burst=1, seq=1,
                              orig_name=self.current_image_path.name)
        name = render_name(self._current_template(), fields)
        mode = "复制" if self.settings.get("rename_mode", "copy") == "copy" else "原地"
        self.rename_label.configure(text=f"{name}   [{mode}]")

    def _do_rename(self):
        if not self.current_image_path or not self.current_result:
            return
        bird = self.current_result["bird"]
        sex = self.sex_var.get()
        if sex and sex != "不标注":
            bird = f"{bird}_{sex}"
        exif = self.current_result.get("exif", {})
        fields = build_fields(exif, bird=bird,
                              latin=self.current_result.get("latin"),
                              loc=self.settings.get("location") or None,
                              burst=1, seq=1,
                              orig_name=self.current_image_path.name)
        new_name = render_name(self._current_template(), fields)
        mode = self.settings.get("rename_mode", "copy")
        rename_dir = Path(self.settings["rename_dir"]) if self.settings.get("rename_dir") else None

        if mode == "inplace":
            if not messagebox.askyesno(
                    "确认原地重命名",
                    f"将把原文件改名为:\n{new_name}\n\n原地重命名会改动原始 RAW，确认继续？"):
                return
            result = apply_rename(self.current_image_path, new_name,
                                  mode="inplace", dry_run=False)
            if result:
                messagebox.showinfo("完成", f"已重命名:\n{result.name}")
                self.current_image_path = result
        else:
            result = apply_rename(self.current_image_path, new_name,
                                  mode="copy", rename_dir=rename_dir, dry_run=False)
            if result:
                messagebox.showinfo("完成", f"已复制为新文件（原文件未动）:\n{result}")

    # ---------- 设置页 ----------

    def _show_settings(self):
        self._clear_content()
        self._highlight_nav(self.btn_settings)

        frame = ctk.CTkFrame(self.content)
        frame.pack(fill="both", expand=True, padx=30, pady=30)

        ctk.CTkLabel(frame, text="设置", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(20, 15))

        # 阈值
        row1 = ctk.CTkFrame(frame, fg_color="transparent")
        row1.pack(fill="x", pady=8)
        ctk.CTkLabel(row1, text="识别置信度阈值:", width=150).pack(side="left")
        self.conf_slider = ctk.CTkSlider(row1, from_=0.3, to=0.9, number_of_steps=12)
        self.conf_slider.set(self.settings["conf_threshold"])
        self.conf_slider.pack(side="left", padx=10)
        self.conf_label = ctk.CTkLabel(row1, text=f"{self.settings['conf_threshold']:.2f}")
        self.conf_label.pack(side="left")
        self.conf_slider.configure(command=lambda v: self.conf_label.configure(text=f"{v:.2f}"))

        # TopN（用户自定义，正整数）
        row2 = ctk.CTkFrame(frame, fg_color="transparent")
        row2.pack(fill="x", pady=8)
        ctk.CTkLabel(row2, text="每物种保留张数:", width=150).pack(side="left")
        self.topn_var = ctk.StringVar(value=str(self.settings["top_n"]))
        self.topn_entry = ctk.CTkEntry(row2, textvariable=self.topn_var, width=80)
        self.topn_entry.pack(side="left")
        ctk.CTkLabel(row2, text="张（正整数，如 3、10、20）").pack(side="left", padx=(6, 0))

        # 地区
        row3 = ctk.CTkFrame(frame, fg_color="transparent")
        row3.pack(fill="x", pady=8)
        ctk.CTkLabel(row3, text="拍摄地区:", width=150).pack(side="left")
        self.region_var = ctk.StringVar(value=REGIONS.get(self.settings["region"], self.settings["region"]))
        region_values = list(REGIONS.values())
        region_codes = list(REGIONS.keys())
        self.region_menu = ctk.CTkOptionMenu(row3, variable=self.region_var, values=region_values)
        self.region_menu.pack(side="left")

        # 画质评分权重（锐度/曝光/占比，相对比例，程序内自动归一化）
        def _make_weight_row(label, key, default):
            row = ctk.CTkFrame(frame, fg_color="transparent")
            row.pack(fill="x", pady=8)
            ctk.CTkLabel(row, text=label, width=150).pack(side="left")
            slider = ctk.CTkSlider(row, from_=0.0, to=1.0, number_of_steps=20)
            slider.set(float(self.settings.get(key, default)))
            slider.pack(side="left", padx=10)
            val = ctk.CTkLabel(row, text=f"{float(self.settings.get(key, default)):.2f}")
            val.pack(side="left")
            slider.configure(command=lambda v, l=val: l.configure(text=f"{v:.2f}"))
            return slider

        self.sharp_slider = _make_weight_row("锐度权重:", "sharp_weight", 0.5)
        self.exposure_slider = _make_weight_row("曝光权重:", "exposure_weight", 0.3)
        self.area_slider = _make_weight_row("占比权重:", "area_weight", 0.2)
        ctk.CTkLabel(frame, text="三个权重是相对比例，会自动归一化（默认 0.5 / 0.3 / 0.2）",
                     text_color="gray", font=ctk.CTkFont(size=11)).pack(pady=(0, 4))

        # 分类方式开关（前面留 150 空标签占位，与上方「占比权重」等文字对齐）
        row_mb = ctk.CTkFrame(frame, fg_color="transparent")
        row_mb.pack(fill="x", pady=8)
        ctk.CTkLabel(row_mb, text="", width=150).pack(side="left")
        self.multi_bird_switch = ctk.CTkSwitch(
            row_mb, text="一张图多种鸟时，各归一类（默认只取主体）")
        self.multi_bird_switch.pack(side="left")
        if self.settings.get("multi_bird"):
            self.multi_bird_switch.select()

        row_ca = ctk.CTkFrame(frame, fg_color="transparent")
        row_ca.pack(fill="x", pady=8)
        ctk.CTkLabel(row_ca, text="", width=150).pack(side="left")
        self.classify_all_switch = ctk.CTkSwitch(
            row_ca, text="只分类不精选（不按 Top N 筛选，全部归档）")
        self.classify_all_switch.pack(side="left")
        if self.settings.get("classify_all"):
            self.classify_all_switch.select()

        # GPU 加速开关
        row_gpu = ctk.CTkFrame(frame, fg_color="transparent")
        row_gpu.pack(fill="x", pady=8)
        ctk.CTkLabel(row_gpu, text="", width=150).pack(side="left")
        self.use_gpu_switch = ctk.CTkSwitch(
            row_gpu, text="使用 GPU 加速识别（NVIDIA 显卡，关闭则强制 CPU）")
        self.use_gpu_switch.pack(side="left")
        if self.settings.get("use_gpu", True):
            self.use_gpu_switch.select()

        # ---------- EXIF 重命名 ----------
        ctk.CTkLabel(frame, text="EXIF 重命名", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(20, 6))

        row_rp = ctk.CTkFrame(frame, fg_color="transparent")
        row_rp.pack(fill="x", pady=8)
        ctk.CTkLabel(row_rp, text="命名预设:", width=150).pack(side="left")
        self.rename_preset_var = ctk.StringVar(value=self.settings.get("rename_preset", "简洁归档"))
        self.rename_preset_menu = ctk.CTkOptionMenu(
            row_rp, variable=self.rename_preset_var, values=list(PRESETS.keys()))
        self.rename_preset_menu.pack(side="left")

        row_rt = ctk.CTkFrame(frame, fg_color="transparent")
        row_rt.pack(fill="x", pady=8)
        ctk.CTkLabel(row_rt, text="自定义模板:", width=150).pack(side="left")
        self.custom_template_entry = ctk.CTkEntry(
            row_rt, placeholder_text="如 {bird}_{date}_{orig}{ext}（留空用预设）")
        self.custom_template_entry.pack(side="left", fill="x", expand=True, padx=5)
        if self.settings.get("custom_template"):
            self.custom_template_entry.insert(0, self.settings["custom_template"])

        row_rm = ctk.CTkFrame(frame, fg_color="transparent")
        row_rm.pack(fill="x", pady=8)
        ctk.CTkLabel(row_rm, text="", width=150).pack(side="left")
        self.rename_mode_switch = ctk.CTkSwitch(
            row_rm, text="原地重命名（默认关闭=复制重命名，保护原始 RAW）")
        self.rename_mode_switch.pack(side="left")
        if self.settings.get("rename_mode") == "inplace":
            self.rename_mode_switch.select()

        row_rd = ctk.CTkFrame(frame, fg_color="transparent")
        row_rd.pack(fill="x", pady=8)
        ctk.CTkLabel(row_rd, text="复制目标目录:", width=150).pack(side="left")
        self.rename_dir_entry = ctk.CTkEntry(row_rd, placeholder_text="留空=源目录下 renamed/")
        self.rename_dir_entry.pack(side="left", fill="x", expand=True, padx=5)
        if self.settings.get("rename_dir"):
            self.rename_dir_entry.insert(0, self.settings["rename_dir"])
        ctk.CTkButton(row_rd, text="浏览", width=60,
                      command=self._pick_rename_dir).pack(side="left")

        row_loc = ctk.CTkFrame(frame, fg_color="transparent")
        row_loc.pack(fill="x", pady=8)
        ctk.CTkLabel(row_loc, text="拍摄地点:", width=150).pack(side="left")
        self.location_entry = ctk.CTkEntry(row_loc, placeholder_text="模板 {loc} 用，留空读 GPS（读不到→UnknownLoc）")
        self.location_entry.pack(side="left", fill="x", expand=True, padx=5)
        if self.settings.get("location"):
            self.location_entry.insert(0, self.settings["location"])

        # 连拍识别开关
        row_bd = ctk.CTkFrame(frame, fg_color="transparent")
        row_bd.pack(fill="x", pady=8)
        ctk.CTkLabel(row_bd, text="", width=150).pack(side="left")
        self.burst_detect_switch = ctk.CTkSwitch(
            row_bd, text="连拍识别（佳能原生标签 / 其余品牌按时间+相似度分组）")
        self.burst_detect_switch.pack(side="left")
        if self.settings.get("burst_detect", True):
            self.burst_detect_switch.select()

        # 保存按钮
        def save():
            try:
                top_n = int(str(self.topn_var.get()).strip())
                if top_n < 1:
                    raise ValueError
            except (ValueError, TypeError):
                messagebox.showerror("错误", "每物种保留张数需为正整数（≥1）")
                return
            self.settings["conf_threshold"] = round(self.conf_slider.get(), 2)
            self.settings["top_n"] = top_n
            self.settings["sharp_weight"] = round(self.sharp_slider.get(), 2)
            self.settings["exposure_weight"] = round(self.exposure_slider.get(), 2)
            self.settings["area_weight"] = round(self.area_slider.get(), 2)
            self.settings["multi_bird"] = bool(self.multi_bird_switch.get())
            self.settings["classify_all"] = bool(self.classify_all_switch.get())
            self.settings["use_gpu"] = bool(self.use_gpu_switch.get())
            # 反查地区代码
            for code, name in REGIONS.items():
                if name == self.region_var.get():
                    self.settings["region"] = code
                    break
            # EXIF 重命名设置
            self.settings["rename_preset"] = self.rename_preset_var.get()
            self.settings["custom_template"] = self.custom_template_entry.get().strip()
            self.settings["rename_mode"] = "inplace" if self.rename_mode_switch.get() else "copy"
            self.settings["rename_dir"] = self.rename_dir_entry.get().strip()
            self.settings["location"] = self.location_entry.get().strip()
            self.settings["burst_detect"] = bool(self.burst_detect_switch.get())
            save_settings(self.settings)
            messagebox.showinfo("已保存", "设置已保存")

        ctk.CTkButton(frame, text="保存设置", command=save, height=36).pack(pady=20)

        # 图鉴库信息
        ctk.CTkLabel(frame, text=f"图鉴库: 已收录 {len(self.guide)} 种鸟",
                     text_color="gray").pack(pady=5)
        ctk.CTkLabel(frame, text=f"图鉴目录: {GALLERY_DIR}",
                     text_color="gray", font=ctk.CTkFont(size=11)).pack()

    # ---------- 导航高亮 ----------

    def _highlight_nav(self, active_btn):
        for btn in [self.btn_batch, self.btn_single, self.btn_settings]:
            if btn == active_btn:
                btn.configure(fg_color=["#1f6aa5", "#1f6aa5"])
            else:
                btn.configure(fg_color=["#3b8ed0", "#3b8ed0"])


# ---------- 入口 ----------

if __name__ == "__main__":
    # 检查 exiftool
    if not find_exiftool():
        print("⚠️ 未找到 exiftool。请先安装：")
        print("  Windows: 下载 exiftool.exe 放工具目录或 PATH")
        print("  macOS: brew install exiftool")
        # 不退出，让用户能进设置看

    app = BirdApp()
    app.mainloop()
#（注：内容由AI生成）
