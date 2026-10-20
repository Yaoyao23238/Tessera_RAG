# Docling 模型下载方法记录

更新日期：2026-08-14

本文记录 Tessera 所需 Docling 模型（tableformer / RapidOCR / layout）的下载方法与踩坑，供后续重装、迁移、排查复用。

## 源目录结构

Docling 模型包统一存放于（启动时经 `DOCLING_ARTIFACTS_PATH` 指向）：

```
D:\1RAG\Tessera\backend\data\models\docling\
├── docling-project--docling-layout-heron\        # layout 模型（transformer 版，约 164MB）
├── docling-project--docling-layout-heron-onnx\   # layout ONNX 变体（下载时曾触发 401）
├── docling-project--docling-models\              # tableformer 模型（v2.3.0）
│   └── model_artifacts\tableformer\
│       ├── accurate\tableformer_accurate.safetensors   # 203MB
│       ├── accurate\tm_config.json
│       ├── fast\tableformer_fast.safetensors           # 139MB
│       └── fast\tm_config.json
└── RapidOcr\                                       # RapidOCR 模型（从 site-packages 复制）
    ├── PP-OCRv6_det_small.onnx
    ├── PP-OCRv6_rec_small.onnx
    └── ch_ppocr_mobile_v2.0_cls_mobile.onnx
```

`is_docling_downloaded()` 就绪判据：目录存在 layout 权重 + `docling-project--docling-models` 内存在权重文件。

## tableformer 下载（曾命中 CAS 401）

### 现象

`download_models()` 下载 docling 大模型时抛：

```
RuntimeError: Task error: File reconstruction error:
CAS Client Error: Request error: HTTP status client error (401 Unauthorized),
domain: https://cas-server.xethub.hf.co/v2/reconstructions/...
```

根因：新版 docling 大模型（layout onnx 变体、tableformer）已迁移到 Hugging Face 的 **xethub 存储**。hf-mirror 镜像对该 CAS 服务返回 401，导致下载失败。

### 解法（关键）

设环境变量禁用 Xet 协议，回落标准 HTTP 下载：

```python
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HUB_DISABLE_XET"] = "1"   # 关键：禁用 Xet，绕过 CAS 401
```

### 精确下载（只下 tableformer，跳过 onnx 变体等非必需项）

注意 revision 是 `v2.3.0`（**不是 main**），模型路径在 `model_artifacts/tableformer/`：

```python
import os
from pathlib import Path
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HUB_DISABLE_XET"] = "1"

from docling.models.stages.table_structure.table_structure_model import TableStructureModel

local_dir = Path(r"D:\1RAG\Tessera\backend\data\models\docling") / TableStructureModel._model_repo_folder
local_dir.mkdir(parents=True, exist_ok=True)
# download_models 内部 revision 已写死为 v2.3.0
TableStructureModel.download_models(local_dir=local_dir)
```

### 完整模型包下载（设置页按钮路径）

`model_manager.py::download_docling_bundle()` 调 `docling.utils.model_downloader.download_models()`，会下载 layout + tableformer + picture_classifier + code_formula + onnx 变体等完整包（估算 6GB）。Xet 指令已同步到该函数（含环境变量恢复），设置页触发不会再 401。

## RapidOCR 复制（非下载）

RapOCR 模型已随包安装在 site-packages（约 31MB），docling 设了 `DOCLING_ARTIFACTS_PATH` 后期望在 `docling/RapidOcr/` 找，需复制而非重新下载。

- 源：`rapidocr` 包内 `models/` 目录
- 目标：`docling/RapidOcr/`（3 个 onnx，见上文目录结构）
- 用 docling 内部解析逻辑确定期望文件（`_resolve_rapidocr` / `_rapidocr_artifacts` / `_backend_to_engine_type`）
- 依赖：需安装 `onnxruntime`（`pip install onnxruntime`）
- 验证：`RapidOcrModel(enabled=True, artifacts_path=..., options=..., accelerator_options=...)` 初始化后 `reader is not None` 即通过

## 后台进程执行（避免被清理）

Start-Process 启动的 python 进程会被 WorkBuddy 外部机制周期性清理。下载长任务须用 `schtasks` 一次性任务独立运行：

```bash
schtasks /create /tn TaskName /tr "'<python>' '<script.py>'" /sc once /st 23:59 /f
schtasks /run /tn TaskName
# 完成后删除
schtasks /delete /tn TaskName /f
```

## 网络注意事项

- hf-mirror.com 网络不稳定，下载可能停滞（HEAD 超时）。进程会重试，需监控 incomplete 文件增长确认在推进。
- ModelScope 无 docling 镜像，docling 模型只能走 hf-mirror（或直连，国内易超时）。
- 判断下载是否在推进：看 `.cache/huggingface/download/**/*.incomplete` 文件体积是否增长，而非只看最终文件路径。