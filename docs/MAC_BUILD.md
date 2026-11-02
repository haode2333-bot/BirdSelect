# macOS 打包与使用指南

本工具已适配 macOS（含 Apple Silicon 原生加速）。本文档说明如何在 Mac 上
运行、以及如何打包成可分发版本。**打包必须在 macOS 机器上执行**，Windows 无法交叉构建。

## 一、特性说明（Mac 上自动生效）

- 识别推理：Apple Silicon（M 系列）自动走 **CoreML Execution Provider**
  （底层用 ANE/Metal），无需配置；Intel Mac 走 CPU。
- RAW 预览提取：优先 **rawpy（libraw）**，arm64 原生构建，比 exiftool（perl）快很多。
- exiftool 走 Homebrew（`brew install exiftool`），在 PATH 里，`toolpaths.find_exiftool()` 会自动找到。
- 模型路径：`~/.snipe/models/`（`toolpaths.resolve_model_paths()` 对非 Windows 走这里）。
- 内存保护：批量处理串行 + 预览缓存限量（`--max-preview-cache`），不会吃满统一内存。
- 硬盘休眠保护：`--read-gap` 加间隔停顿，适合长时间跑移动硬盘。

## 二、环境准备

```bash
# 1. exiftool（RAW 元数据读取）
brew install exiftool

# 2. Python 3.12+（推荐用 uv 管理，python-build-standalone 可搬迁）
brew install uv
uv python install 3.12
```

## 三、直接运行（开发 / 自用）

```bash
git clone https://github.com/haode2333-bot/BirdSelect.git
cd BirdSelect

# 建 venv 并装依赖
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -r requirements.txt

# 下载识别模型（约 287MB，首次一次）
snipe init

# 启动 GUI（二选一）
python3 bird_desktop.py
# 或：chmod +x 启动观鸟工具.command && ./启动观鸟工具.command
```

## 四、打包「便携版」（可搬迁 Python 文件夹，推荐）

原理同 Windows 便携版：用 uv 的 python-build-standalone（本身可搬迁），依赖装进它的
site-packages，整目录拷走即用，目标机器无需装任何东西。

```bash
# 1. 找到 uv 下载的可搬迁 Python（macOS arm64）
uv python list        # 记下路径，形如 .../cpython-3.12.x-macos-aarch64-none/

# 2. 拷贝到目标目录
DST=~/BirdSelect-mac
mkdir -p "$DST"
cp -r <上面 uv python 路径> "$DST/python"

# 3. 装依赖（关键 --break-system-packages，standalone 被标记 externally-managed）
uv pip install --python "$DST/python/bin/python3" --break-system-packages \
  -r requirements.txt

# 4. 拷源码 + 资源
cp bird_desktop.py bird_pick.py bird_guide.py exif_rename.py \
   rename_engine.py burst_group.py gpu_preload.py toolpaths.py "$DST/"
cp -r data "$DST/"
# exiftool 走 brew，不必打包；要离线可把 brew 的 exiftool 拷到 "$DST/exiftool/"
# 模型：把 ~/.snipe/models/ 拷到 "$DST/models/"

# 5. 启动器
cp 启动观鸟工具.command "$DST/"
chmod +x "$DST/启动观鸟工具.command"
```

验证可搬迁：`"$DST/python/bin/python3" -c "import sys; print(sys.prefix)"` 应输出 `$DST/python`。

## 五、打包「.app」（PyInstaller onedir）

要双击即用的 .app 用这个（onedir 比 onefile 合适：模型 287MB 塞单个文件会到 600MB+ 且每次解压慢）。

```bash
cd BirdSelect
uv venv --python 3.12 build-venv
uv pip install --python build-venv/bin/python "snipe-cli[cpu]" opencv-python-headless customtkinter pyinstaller

build-venv/bin/python -m PyInstaller --noconfirm --onedir --windowed --name BirdSelect \
  --collect-all snipe --collect-all customtkinter --collect-all onnxruntime \
  --collect-all rawpy --hidden-import bird_pick --hidden-import gpu_preload \
  bird_desktop.py
```

产物 `dist/BirdSelect.app`（macOS 的 --windowed 生成 .app bundle）。再把
`models/`、`exiftool/`（可选，走 brew 可不打包）、`data/` 拷到 app 旁的资源目录。

**冻结模式路径坑（Mac 同 Windows）**：打包后 `__file__` 指向 `_internal`，`sys.executable`
才是 app 位置。代码里已统一用 `toolpaths.app_dir()`（frozen 时取 `sys.executable` 父目录），
可写文件（settings.json）和外部资源（models/exiftool/data）都放 exe 旁，不要用 `__file__`。

**批量扫描子进程坑**：冻结模式下 GUI 的「开始扫描」是进程内调用 `bird_pick.main()`
（不是 subprocess 再起一个 GUI），`--hidden-import bird_pick --hidden-import gpu_preload`
必须加，否则 PyInstaller 不打包这两个只被 import 的模块。

## 六、Apple Silicon 加速（CoreML）验证

```bash
python3 -c "import onnxruntime as ort; print(ort.get_available_providers())"
# 应含 'CoreMLExecutionProvider'（M 系列）
```

启动工具后，批量扫描日志会打印「识别后端: GPU 加速（Apple Silicon / Metal）」
即表示走了 CoreML；否则显示 CPU 模式。

## 七、测试清单（Mac 上过一遍再发）

- [ ] `python3 -c "import bird_desktop"` 无报错，GUI 能起。
- [ ] `brew list exiftool` 存在，`python3 exif_rename.py <某张RAW> --bird 测试 --dry-run` 正常。
- [ ] 拿 CR3 / NEF / ARW / RAF 各一张跑 `bird_pick.py --scan-dir ... --dry-run`，识别出鸟且 quality_total 非零。
- [ ] 连拍分组：`python3 exif_rename.py --from-index bird_index.csv --preset 简洁归档 --dry-run` 看到「连拍组/序号」。
- [ ] 复制重命名默认不碰原文件；原地重命名有确认弹窗。
- [ ] 打包版 .app 双击能起、设置可写（settings.json 落在 app 旁，不是 _internal）。

## 八、已知坑

- exiftool 提取二进制 tag（JpgFromRaw/PreviewImage）必须 `-b` 走 stdout，用 `-o` 会报
  `Can't create JPEG files from other types`（代码已规避）。
- snipe 的 loguru 默认打 DEBUG 到 stderr，代码已 `suppress_snipe_logging()` 只留 ERROR。
- 便携版用 brew 的 exiftool 依赖 Perl 环境；要「真离线」需把 exiftool 及其 perl 依赖一起带，
  否则建议目标机器 `brew install exiftool`。
- 若 CoreML EP 对某模型报错（onnxruntime 版本差异），`gpu_preload.ensure_gpu()` 会静默回退 CPU，不影响功能。
