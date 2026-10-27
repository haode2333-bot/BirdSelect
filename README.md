# BirdSelect 观鸟影像选片工具

> 作者：Stark Xu

本地运行的拍鸟照片自动识别与选片工具。扫描硬盘里的 RAW 照片，自动识别鸟种、按画质打分、按鸟种分类归档，帮你从成百上千张连拍里挑出最佳几张。

纯本地运行，不上传照片、不依赖云端 API。支持佳能 / 尼康 / 索尼三大主流 RAW 格式。

## 功能特性

- **鸟种识别**：基于本地 ONNX 模型（snipe），自动检测画面中的鸟并识别物种（中文名 + 拉丁名），可按省份过滤常见鸟种
- **批量选片**：递归扫描整个文件夹，按鸟种分组，每类按画质分（锐度 50% + 曝光 30% + 鸟体占比 20%）自动挑 Top N 张
- **单张识别**：打开单张照片，识别 + 图鉴对比 + 雌雄异形提示 + 手动标注性别/亚成
- **EXIF 重命名**：读拍摄时间，自动重命名成「鸟种名_日期时间_原文件名」格式
- **断点续扫**：记录每张文件的 size+mtime，重跑自动跳过已处理照片，中断后可从断点继续
- **画质打分**：只在鸟体区域内算锐度/曝光/占比，生态摄影选片专用

## 支持格式

| 厂商 | 格式 |
|------|------|
| 佳能 | CR3 |
| 尼康 | NEF |
| 索尼 | ARW |
| 通用 | JPG / JPEG / PNG |

## 快速开始（普通用户，无需装任何环境）

1. 到 [Releases](../../releases) 页面下载最新的「观鸟工具-便携版.zip」（约 660MB，已内置识别模型和运行环境）
2. 解压到任意目录
3. 双击 `启动观鸟工具.bat`

适用于 Windows 10 / 11 64 位，无需安装 Python、无需联网、无需显卡。

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
python bird_desktop.py
```

有 NVIDIA 显卡想加速识别的话，可额外 `pip install onnxruntime-gpu`（识别约 0.4 秒/张；CPU 约 4.6 秒/张）。

命令行批量模式（可配合脚本 / 服务器使用）：

```bash
python bird_pick.py --scan-dir /path/to/photos --output-dir /path/to/out --top-n 3
```

## 界面

（截图占位，可在此补充软件运行截图）

- 左侧导航：批量扫描 / 单张识别 / 设置
- 批量扫描：选择扫描目录与输出目录 → 开始扫描 → 进度条 + 运行日志
- 单张识别：选照片 → 识别 → 左侧你的照片 / 右侧图鉴参考 → 标注性别 → 重命名

## 打包便携版（可复现）

便携版 = 可搬迁的独立 Python 运行时 + 全部依赖 + 内置模型 + exiftool，整个文件夹拷走即用，无需目标机器装任何东西。步骤：

1. 用 `uv` 取一个 python-build-standalone 运行时（本身可搬迁），拷贝到目标目录的 `python/` 下
2. `uv pip install --python python/python.exe --break-system-packages "snipe-cli[cpu]" opencv-python-headless customtkinter`
3. 拷贝本仓库源码、`data/`、`exiftool/` 到目标目录
4. 把模型（`snipe init` 下载到 `%APPDATA%/.snipe/models/`）拷到目标目录 `models/`
5. 写一个启动 `.bat`（先 `python.exe` 预检 import，成功后 `pythonw.exe` 启动 GUI）

## 许可证

本项目以 **GNU Affero General Public License v3.0（AGPL-3.0）** 发布，详见 [LICENSE](LICENSE)。

原因：识别引擎 snipe 及其模型采用 AGPL-3.0，本工具作为其衍生作品需遵循同一许可证。第三方组件声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 致谢

- [snipe](https://github.com/woolen-sheep/snipe) —— 鸟体检测与物种识别引擎（AGPL-3.0）
- [exiftool](https://exiftool.org/) —— EXIF 读取与 RAW 预览提取
