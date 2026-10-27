# 第三方组件声明

BirdSelect 使用了以下开源组件，特此致谢并按其许可证要求声明。

## snipe（识别引擎 + 识别模型）

- 项目：https://github.com/woolen-sheep/snipe
- 许可证：GNU Affero General Public License v3.0（AGPL-3.0）
- 用途：鸟体检测 + 物种识别。本工具 `import snipe` 调用其 `detect_birds()`。
- 识别模型（yolo26x-seg.onnx、classifier.onnx）同样来自 snipe 项目的 releases，随项目以 AGPL-3.0 分发。
- 完整许可证文本见本仓库根目录 `LICENSE`（AGPL-3.0）。

## exiftool

- 项目：https://exiftool.org/
- 作者：Phil Harvey
- 许可证：Perl 双许可证（GPL v1+ 或 Artistic License，可选其一）
- 用途：读取 EXIF 拍摄信息、提取 RAW 内嵌 JpgFromRaw 预览用于画质打分。
- exiftool 不随源码仓库分发；开发者请自行安装（Windows 下载 exiftool.exe，macOS `brew install exiftool`）。便携版 Release 中随包附带了 exiftool.exe 及其许可文件。

## 其他 Python 依赖（运行时）

| 依赖 | 许可证 | 用途 |
|------|--------|------|
| onnxruntime | MIT | ONNX 模型推理 |
| opencv-python-headless | Apache-2.0 | 画质打分（拉普拉斯锐度、直方图） |
| rawpy | LGPL-2.1 | RAW 解码（snipe 依赖） |
| pillow | HPND | 图像读写 |
| customtkinter | MIT | 桌面 GUI |
| numpy | BSD-3-Clause | 数值计算 |
| pydantic | MIT | snipe 配置模型 |
| pywebview | BSD-3-Clause | snipe 预览窗口 |

## 图鉴数据

`data/bird_taxonomy.csv` 为项目自带的中国鸟类图鉴索引（中文名/拉丁名/分类/雌雄异形/羽色描述），可按需自行扩充。
