# BirdSelect 观鸟影像选片工具

> 作者：Stark Xu

本地运行的拍鸟照片自动识别与选片工具。扫描硬盘里的 RAW 照片，自动识别鸟种、按画质打分、按鸟种分类归档，帮你从成百上千张连拍里挑出最佳几张。

纯本地运行，不上传照片、不依赖云端 API。支持 Windows 与 macOS（Apple Silicon 原生加速）。

## 功能特性

- **鸟种识别**：基于本地 ONNX 模型（snipe），自动检测画面中的鸟并识别物种（中文名 + 拉丁名），可按省份过滤常见鸟种
- **批量选片**：递归扫描整个文件夹，按鸟种分组，每类按画质分（锐度 50% + 曝光 30% + 鸟体占比 20%）自动挑 Top N 张
- **连拍分组**：自动识别连拍——佳能读原生标签（BurstUUID 等），其余品牌按「时间 + 图像相似度」分组，结果写进索引表（可在设置里开关）
- **模板化 EXIF 重命名**：内置多套命名预设 + 自定义模板，支持「复制重命名」（默认，保护原始 RAW）与「原地重命名」（可选）
- **单张识别**：打开单张照片，识别 + 图鉴对比 + 雌雄异形提示 + 手动标注性别/亚成
- **断点续扫**：记录每张文件的 size+mtime，重跑自动跳过已处理照片，中断后可从断点继续
- **画质打分**：只在鸟体区域内算锐度/曝光/占比，生态摄影选片专用

## 支持格式

| 厂商 | 格式 |
|------|------|
| 佳能 | CR3 / CR2 |
| 尼康 | NEF / NRW |
| 索尼 | ARW / SR2 |
| 富士 | RAF |
| 松下 | RW2 |
| 奥林巴斯 / OM | ORF |
| 宾得 / 通用 | PEF / DNG |
| 徕卡 | RWL / DNG |
| 通用 | JPG / JPEG / PNG |

## 快速开始（普通用户，无需装任何环境）

到 [Releases](../../releases) 页面下载最新版（v1.1.0）：

| 版本 | 适用 | 大小 | 识别速度 |
|------|------|------|----------|
| BirdSelect-portable-CPU.zip | 任何 Win10/11 64 位电脑，无需显卡 | 约 660MB | 约 4-6 秒/张 |
| BirdSelect-portable-GPU.zip | 有 NVIDIA 显卡（GTX 10 系及以上） | 约 2GB | 约 0.4 秒/张 |

下载后解压到任意目录，双击 `启动观鸟工具.bat` 即可。

纯本地运行：无需安装 Python、无需联网、不依赖云端 API。

## 快速开始（开发者）

前置要求：Python 3.12+，以及 exiftool。

```bash
# 1. 安装 exiftool
#    Windows: 下载 exiftool.exe 放到 PATH 或本目录
#    macOS:   brew install exiftool

# 2. 安装依赖
pip install -r requirements.txt

# 3. 下载识别模型（约 287MB，首次运行一次）
snipe init

# 4. 启动 GUI
python bird_desktop.py        # Windows
# macOS 双击 启动观鸟工具.command，或 python3 bird_desktop.py
```

有 NVIDIA 显卡想加速识别的话，可额外 `pip install onnxruntime-gpu`（识别约 0.4 秒/张；CPU 约 4.6 秒/张）。

### macOS / Apple Silicon 专属

- 自动检测 M 系列芯片，识别推理走 **CoreML Execution Provider**（底层用 ANE / Metal 加速），无需额外配置
- RAW 预览提取优先走 **rawpy（libraw）**，arm64 原生构建，比 exiftool（perl 脚本）快得多
- **内存保护**：批量处理串行 + 预览缓存限量（`--max-preview-cache`），不会一次加载几十张 RAW 吃满统一内存
- **硬盘休眠保护**：长时间跑移动硬盘时可用 `--read-gap` 加间隔停顿，减少硬盘频繁启停/发热

## 命令行

批量扫描：

```bash
python bird_pick.py --scan-dir /path/to/photos --output-dir /path/to/out --top-n 3
```

常用参数：`--classify-all`（全部归档不选 Top）、`--dry-run`（只识别不复制）、
`--resume`（断点续扫）、`--no-burst-detect`（关连拍分组）、
`--read-gap 0.2`（硬盘休眠保护）、`--max-preview-cache 300`（内存保护）。

EXIF 模板化重命名（连拍分组 + 复制重命名）：

```bash
# 批量：读 bird_index.csv，按鸟种 → 连拍分组 → 模板重命名
python exif_rename.py --from-index bird_index.csv --preset 简洁归档

# 自定义模板
python exif_rename.py --from-index bird_index.csv --template "{bird}_{date}_{loc}{ext}" --location 深圳南山

# 原地重命名（默认是 copy，保护原始 RAW）
python exif_rename.py --from-index bird_index.csv --mode inplace

# 先 dry-run 看效果
python exif_rename.py --from-index bird_index.csv --dry-run
```

### 命名预设与模板

内置三套预设，可在设置里随时切换：

| 预设 | 模板 | 示例 |
|------|------|------|
| 简洁归档 | `{datetime}_{bird}_连拍组{burst}_序号{seq}{ext}` | `20261102_153020_灰背鸫_连拍组001_序号02.CR3` |
| 上传图鉴 | `{bird}_{date}_{loc}_序号{num}{ext}` | `灰背鸫_20261102_深圳南山_序号01.CR3` |
| 保留相机序号 | `{orig}_{bird}_{date}{ext}` | `DSC01234_黑脸噪鹛_20261102.CR3` |
| 保持原文件名 | `{orig}{ext}` | `IMG_0076.CR3` |

可用占位符：`{date}` `{time}` `{datetime}` `{bird}` `{latin}` `{loc}` `{burst}` `{seq}` `{num}` `{orig}` `{model}` `{ext}`。

EXIF 缺失兜底：读不到拍摄时间 → `UnknownTime`，读不到地点 → `UnknownLoc`，不会中断。

## 界面

- 左侧导航：批量扫描 / 单张识别 / 设置
- 批量扫描：选择扫描目录与输出目录 → 开始扫描 → 进度条 + 运行日志
- 单张识别：选照片 → 识别 → 左侧你的照片 / 右侧图鉴参考 → 标注性别 → 重命名
- 设置：置信阈值 / TopN / 地区 / 画质权重 / GPU 开关 / 连拍识别开关 / 命名预设与模板

## 打包便携版（可复现）

便携版 = 可搬迁的独立 Python 运行时 + 全部依赖 + 内置模型 + exiftool，整个文件夹拷走即用，无需目标机器装任何东西。步骤：

1. 用 `uv` 取一个 python-build-standalone 运行时（本身可搬迁），拷贝到目标目录的 `python/` 下
2. `uv pip install --python python/python.exe --break-system-packages -r requirements.txt`
3. 拷贝本仓库源码、`data/`、`exiftool/` 到目标目录
4. 把模型（`snipe init` 下载到 `%APPDATA%/.snipe/models/`）拷到目标目录 `models/`
5. 写一个启动 `.bat`（Windows）/ `.command`（macOS）

## 许可证

本项目以 **GNU Affero General Public License v3.0（AGPL-3.0）** 发布，详见 [LICENSE](LICENSE)。

原因：识别引擎 snipe 及其模型采用 AGPL-3.0，本工具作为其衍生作品需遵循同一许可证。第三方组件声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 致谢

- [snipe](https://github.com/woolen-sheep/snipe) —— 鸟体检测与物种识别引擎（AGPL-3.0）
- [exiftool](https://exiftool.org/) —— EXIF 读取与 RAW 预览提取
- [rawpy / libraw](https://github.com/letmaik/rawpy) —— 跨品牌 RAW 缩略图提取
