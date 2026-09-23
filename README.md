# RAG 文件助手 · 本地文档问答

> 把你电脑里的笔记和文档索引进向量库，用自然语言提问，答案带出处。全程本地推理，文件不出机器。

![License](https://img.shields.io/badge/license-MIT-blue) ![Python](https://img.shields.io/badge/Python-3.10%2B-yellow) ![Platform](https://img.shields.io/badge/platform-Windows-lightgrey) ![GPU](https://img.shields.io/badge/GPU-optional-green)

市面上的 RAG 工具要么把文档传云端，要么要你先装一堆东西。这个的思路是：**一个启动器 + 首次运行自动装环境 + 开源 embedding 本地跑**。

## 功能

| | |
| --- | --- |
| **硬件检测** | CPU / 内存 / GPU 与算力等级，据此推荐模型 |
| **模型部署** | 推荐开源 embedding 与对话模型，一键下载部署（权重不入库，运行时联网取） |
| **个人空间** | 选一个文件夹作为知识库，文本文件分块向量化，优先用 GPU |
| **增量索引** | 文件变动自动补索引，不用全量重建 |
| **RAG 问答** | 检索增强回答 + 引用来源标注，多对话管理 |
| **三栏界面** | 类 Linkly 布局：对话 / 笔记 / 找文件，日夜间双主题，可跟随 Life System 的配色 |
| **托盘与快捷键** | 常驻托盘，全局快捷键唤起 |

## 架构：轻量启动器 + 外部 Python 环境

exe **不打包 Python 环境和依赖**，它只是一个约 27MB 的启动器，内置主程序源码：

```
RAGAssistant.exe（启动器）
    │  首次启动
    ├─ 显示「环境初始化」窗口
    ├─ 下载 Python（~25MB）+ 安装依赖（~200MB）到环境目录（默认 D:\RAGAssistant）
    ├─ 解压主程序源码到 <环境目录>\app
    └─ 之后每次都直接启动主程序
```

这样做的好处：改代码后重打启动器约 1 分钟；发给别人只要这一个 exe，对方首次运行自己装环境；换模型或卸载依赖不动启动器。环境目录可在初始化界面里改。

## 源码运行

```bash
python install.py    # 自动检测 GPU 并装依赖
python main.py       # 直接跑主程序
```

打包：

```bash
python build.py              # 目录模式（推荐）
python build.py --onefile    # 单文件
python build.py --console    # 带控制台，便于排查
```

## 技术栈

| 模块 | 技术 |
| --- | --- |
| GUI | PySide6（Qt6，三栏布局，日/夜双主题 QSS） |
| Embedding | fastembed + onnxruntime（ONNX 推理，体积小） |
| 向量检索 | FAISS（可选），无 FAISS 时退化为 numpy 暴力检索 |
| 对话 | OpenAI 兼容 API 或本地 Ollama |

## 数据与密钥

- 索引默认写在环境目录下的 `data/`（`index.faiss` + `metadata.json`），你的原文与索引**不上传任何服务**。
- API Key 走 `.env` 或设置页，落在 `config.json` —— 这两个都已在 `.gitignore` 里，不会进版本库。
- 想清库重建：删掉 `data/` 目录即可。

## GPU 说明

- 走 GPU 需要 NVIDIA 驱动（>=550）+ CUDA Toolkit 12.x + cuDNN 9。
- 没装系统 CUDA 也不影响使用，自动回落 CPU，只是索引慢一些。
- 设置里可手动切 `auto / cuda / cpu`。

## 项目结构

```
rag/
├── bootstrap.py         # 启动器：环境初始化 + 拉起主程序
├── main.py              # 入口（单实例 / 主题 / QApplication）
├── install.py           # 依赖安装（GPU 自动检测）
├── core/                # 硬件检测 / 模型推荐 / 索引 / 检索 / 对话
├── ui/                  # PySide6 界面层
│   ├── main_window.py   # 主窗口：对话 / 笔记 / 找文件 / 部署 / 设置
│   └── theme.py         # 日/夜双主题（可跟随 Life System）
├── build.py             # 打包轻量启动器
└── make_icon.py         # 图标生成
```

## License

[MIT](LICENSE)

---

用得上的话 **给个 Star** ⭐ 报问题或想要支持的文件格式，开 [Issue](https://github.com/Umbrellalalala/rag/issues)。

同系列：[Life System](https://github.com/Umbrellalalala/life-system)（集成工具页可直接拉起本程序）· [ApiCluster](https://github.com/Umbrellalalala/api-cluster)（给这里提供一个本地模型入口）
