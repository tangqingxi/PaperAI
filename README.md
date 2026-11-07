# PaperAI

本地 PDF 转 Markdown，并让执行 Agent 在同一任务中完成全文视觉核查、数学原位修复和验收。程序不会把提取成功标成精度 PASS。

## 使用

```bash
git clone https://github.com/tangqingxi/PaperAI.git
cd PaperAI
```

把一篇或多篇 PDF 放进 `papers/`，向具备本地文件访问和图像查看能力的 Agent 发送：

> 阅读 AGENTS.md，使用 PaperAI 高精度模式处理 papers 文件夹中的论文。

Agent 会准备 `.venv`，转换、逐页看图、修订并验收。中文路径和含空格文件名可用，PDF 不上传转换服务。首次安装需要网络下载 Python 包；扫描件需要 Agent 视觉识别或另外配置本地 OCR。

每篇结果为 `results/论文名-源哈希/paper.md`、`assets/`、`QA_REPORT.md`。其中 `.paperai/` 是少量隐藏断点缓存和逐页回执，请保留以便恢复。`paper.md` 在核查完成前是待核查稿，以报告状态为准；无第二份正式终稿。

## 命令行

```bash
python -m paperai prepare papers
python -m paperai scan results/论文目录
python -m paperai status results/论文目录
python -m paperai crop results/论文目录 --page 5 --bbox 40 100 280 210
python -m paperai review results/论文目录 --page 5 --receipt receipt.json
python -m paperai finalize results/论文目录
```

`scan` 返回候选和原页证据位置；`status`/`finalize` 检查回执、公式清单、资源和真实字节哈希。未查看全部原页、修订后回执失效、证据变化或关键缺陷均不能 PASS。回执是 Agent 的声明，软件不能证明 Agent 确实看过图片。

## 开发

Python 3.11+；Windows 与 Ubuntu 24.04、Python 3.11/3.13 的四组 CI 已通过，详见 [验证记录](VALIDATION.md)。`python -m unittest discover -s tests -v`。在无论文的环境，合成测试不等于真实论文精度验收。

当前集成 PyMuPDF/PyMuPDF4LLM 2.0 及其 CPU 布局分析（自动依赖包含 ONNX Runtime）。未集成公式专用 OCR。Docling、Pix2Text、PaddleOCR 等尚未在本项目做精度基准测试，因此没有强制安装重叠模型。执行 Agent 可按需使用已授权的本地识别工具，先裁剪，再对照原页修复；不能把其他引擎输出当真值。

## 许可

本项目原创代码为 AGPL-3.0-or-later。PyMuPDF 系列采用 AGPL/商业双许可，参见[官方说明](https://pymupdf.readthedocs.io/en/latest/about.html)。模型权重的许可须单独检查。Docling代码为MIT、Pix2Text代码为MIT，但这些代码许可不自动覆盖各自全部模型，参见[Docling](https://github.com/docling-project/docling)、[Pix2Text](https://github.com/breezedeus/Pix2Text)。
