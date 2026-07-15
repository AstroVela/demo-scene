# Vane 招采审计 Demo 可复现安装设计

日期：2026-07-15
状态：已确认

## 目标

把当前依赖开发者本机固定 Vane 虚拟环境的发布方式，迁移为任何满足已验证平台要求的用户都能从源码仓库独立完成的安装流程。

完成后，新用户只需要：

1. 准备 Ubuntu 24.04 x86_64、Python 3.12 和受支持的 NVIDIA GPU；
2. 在项目自己的 `.venv` 中从 TestPyPI 安装固定版 `vane-ai`；
3. 安装项目声明的 OpenAI client、OCR、Arrow 和测试依赖；
4. 按独立指南启动本地 Qwen2.5-VL OpenAI-compatible 服务；
5. 运行 `python scripts/run_demo.py`；
6. 得到并验证两份预期 JSONL 输出。

文档中的命令必须与真实 launcher、依赖元数据和测试一致，不能依赖开发者主目录、预装 site-packages 或未声明的 Python 包。

## 已确认的设计决策

- 使用“项目环境 + 模型服务环境”两个隔离边界：
  - 项目 `.venv` 只承载 Vane、Demo、OCR、OpenAI client 和测试依赖；
  - 本地模型服务使用独立的 `$HOME/.venvs/procurement-qwen`，避免 vLLM、PyTorch、CUDA 和 Vane 的 Ray/二进制依赖互相污染。
- README 保留最短可执行路径，完整模型服务搭建放入 `docs/local-qwen-service.zh.md`。
- 官方发布验收只承诺 Ubuntu 24.04 x86_64、CPython 3.12 和 NVIDIA CUDA GPU；其他系统只可标记为未验证。
- Vane 固定为 `vane-ai==0.1.0.dev20260714234347`，launcher 继续 fail fast 校验精确运行时标识。
- 不增加自动安装 shell 脚本；安装步骤保持显式、可审查和易于逐步排错。
- 默认 Demo 继续执行真实 OCR 和真实多模态模型调用，不增加 mock fallback。

## 范围

### 包含

- 重写 README 的前置条件、安装、运行、验证和排错入口；
- 新增独立的本地 Qwen2.5-VL 服务搭建指南；
- 在项目依赖中显式声明固定版 `vane-ai` 和 OpenAI Python client；
- 改造 `scripts/run_demo.py`，让其只使用当前虚拟环境；
- 更新 launcher 和 release-shape 测试；
- 更新 README 生成的受版本控制包元数据；
- 从全新虚拟环境执行快速测试和真实 E2E。

### 不包含

- Windows、macOS、WSL、ARM64、ROCm、Intel XPU 或 CPU-only 正式支持；
- Docker 安装路径；
- 公网部署、多用户鉴权、TLS、反向代理或生产级 vLLM 运维；
- Ray runner 的发布验收；当前仍只验收 `runner: local`；
- 自动下载 NVIDIA 驱动或自动修改系统 CUDA 配置；
- 把模型权重、虚拟环境或运行输出提交到仓库。

## 已验证平台契约

### 项目环境

| 项目 | 发布验收值 |
| --- | --- |
| 操作系统 | Ubuntu 24.04 x86_64 |
| glibc | 2.39 |
| Python | CPython 3.12 |
| Vane distribution | `vane-ai==0.1.0.dev20260714234347` |
| `vane.__version__` | `0.1.0.dev20260714234347` |
| DuckDB Python | `0.1.0.dev20260714234347` |
| DuckDB engine | `v1.6.0-dev121` |
| DuckDB source revision | `ca6948529b` |
| OpenAI client | `openai==2.45.0` |

TestPyPI wheel 为 CPython 3.12、Linux x86_64、`manylinux_2_39` 构建，因此 README 不对更旧 glibc、其他 Python 次版本或其他 CPU 架构作可安装承诺。

### 模型服务环境

发布指南固定以下服务端契约：

| 项目 | 指南值 |
| --- | --- |
| Python | CPython 3.12，独立虚拟环境 |
| vLLM | `vllm==0.25.1` |
| 模型 | `Qwen/Qwen2.5-VL-3B-Instruct` |
| 模型 revision | `66285546d2b821cf421d4f5eb2576359d3770cd3` |
| served model name | `Qwen2.5-VL-3B-Instruct` |
| bind address | `127.0.0.1:8001` |
| API key | `dummy` |
| 最大模型上下文 | 4096 tokens |
| 单请求图片上限 | 1 |

vLLM 0.25.1 的官方 supported-models 页面明确列出 `Qwen2_5_VLForConditionalGeneration` 和 `Qwen/Qwen2.5-VL-3B-Instruct`。模型 revision 来自 2026-07-15 查询到的 Hugging Face 官方仓库 SHA，避免 `main` 更新后结果漂移。

硬件发布验收基线为：

- NVIDIA GPU compute capability 7.5 或更高；
- 16 GiB VRAM；
- NVIDIA driver 能运行由 `uv --torch-backend=auto` 选择的 PyTorch CUDA wheel；
- 至少 25 GiB 可用磁盘，用于约 7.5 GB 模型权重、vLLM/PyTorch wheel 和缓存；
- 允许访问 PyPI、TestPyPI 和 Hugging Face Hub。

16 GiB 是本 Demo 的已验证基线，不写成所有 vLLM/Qwen 工作负载的通用最低要求。更小显存配置不属于发布承诺。

## 文档架构

### README

README 按新用户的真实执行顺序组织：

1. 十秒业务故事和预期结果；
2. 已验证环境表；
3. 五分钟快速开始；
4. 本地 Qwen 服务指南链接和三项 preflight；
5. Demo 命令、快速测试和输出校验；
6. 数据流、Vane API 和业务解释；
7. 常见安装问题的短索引；
8. 精确运行时版本表。

README 只保留模型服务的调用契约：

```text
GET http://127.0.0.1:8001/health -> HTTP 200
GET http://127.0.0.1:8001/v1/models -> 包含 Qwen2.5-VL-3B-Instruct
POST http://127.0.0.1:8001/v1/chat/completions -> 接受 image_url 多模态内容
```

CUDA、vLLM、权重下载和服务排错全部链接到独立指南，避免 README 被服务端运维细节淹没。

### `docs/local-qwen-service.zh.md`

独立指南包含：

1. 支持边界和安全提示；
2. `nvidia-smi`、Python、磁盘和端口检查；
3. 创建 `$HOME/.venvs/procurement-qwen`；
4. 安装 `uv`，使用 `--torch-backend=auto` 安装固定版 vLLM；
5. 使用 `hf download --revision ... --local-dir ...` 下载固定模型快照；
6. 使用只绑定 loopback 的 `vllm serve` 启动命令；
7. `/health`、`/v1/models` 和真实图片请求 smoke test；
8. 返回项目 README 运行真实 Demo；
9. CUDA OOM、driver/backend 不兼容、模型名错误、端口占用、权重不完整和代理变量问题；
10. 停止服务、再次启动和升级边界。

服务命令的设计形态为：

```bash
CUDA_VISIBLE_DEVICES=0 vllm serve "$HOME/models/Qwen2.5-VL-3B-Instruct" \
  --served-model-name Qwen2.5-VL-3B-Instruct \
  --host 127.0.0.1 \
  --port 8001 \
  --api-key dummy \
  --dtype bfloat16 \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.85 \
  --limit-mm-per-prompt '{"image": 1}' \
  --generation-config vllm
```

指南明确说明只绑定 `127.0.0.1`。vLLM 的非 `/v1` 运维端点并不全部受 API key 保护，因此不建议把该 Demo 服务直接暴露到公网。

## 安装流程

README 中的项目环境流程固定为：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip

python -m pip install -i https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  vane-ai==0.1.0.dev20260714234347

python -m pip install -r requirements.txt
python -m pip check
```

采用两步安装的原因：

- 第一条命令让 Vane 的 TestPyPI 来源、精确版本和 PyPI fallback 显式可见；
- 第二条命令安装源码项目及全部直接依赖；
- `pyproject.toml` 仍声明 Vane 精确依赖，使包元数据真实；
- Vane 已在第一步安装后，第二步解析 `-e .[test]` 时会复用当前环境中的精确版本。

`requirements.txt` 保持简短，只作为源码安装入口：

```text
# Install the pinned TestPyPI Vane wheel first; see README.md.
-e .[test]
```

## Python 依赖设计

`pyproject.toml` 的运行依赖增加：

```toml
"vane-ai==0.1.0.dev20260714234347",
"openai==2.45.0",
```

其余已有依赖继续负责：

- `onnxruntime`：RapidOCR CPU inference；
- `rapidocr`：stateful OCR actor；
- `pyarrow`：Vane relation 和 Python 边界；
- `pillow`：fixture 图片读取；
- `pyyaml`：严格 runtime 配置。

`openai` 必须是项目的直接依赖，因为当前 pipeline 明确选择 `provider: openai`。不能继续依靠开发者旧 Vane 环境中偶然存在的 OpenAI client。

## Launcher 设计

`scripts/run_demo.py` 从“拼接两个虚拟环境”改为“验证当前虚拟环境”。

### 删除

- 开发者本机固定 Vane 前缀；
- 固定旧 Vane site-packages 路径；
- supplemental `.venv` site-packages 拼接；
- `site.addsitedir` 和自定义 worker `PYTHONPATH`；
- `importlib.metadata.version("vane")` 的旧 distribution 名称。

### 保留并更新

- loopback URL 的 proxy 清理；
- 精确版本 fail-fast；
- `vane.func`、`vane.cls`、`vane.attach_function`、`vane.configure` 和 `vane.ai.prompt` 检查；
- `duckdb.ray_cxx` 检查；
- DuckDB engine/source revision 检查；
- 最终进入同一个 `procurement_audit_sql_demo.cli.main`。

### 新契约

- 使用 `importlib.metadata.version("vane-ai")`；
- 要求 Python 主次版本为 3.12；
- 要求 `vane.__file__` 位于当前 `sys.prefix` 下；
- 错误消息输出当前解释器、当前 prefix、expected/actual 版本和 README 安装命令；
- Vane 的 UDF/AI 子进程由当前 `sys.executable` 启动，并从同一虚拟环境导入依赖。

launcher 不强制虚拟环境目录名必须是 `.venv`，只验证实际解释器和包来源。这样用户可以使用自己的虚拟环境路径，同时 README 仍采用 `.venv` 作为标准示例。

## 错误处理

| 阶段 | 失败 | 用户可执行提示 |
| --- | --- | --- |
| Vane 安装 | 找不到 matching distribution | 检查 Ubuntu 24.04 x86_64、Python 3.12 和两个 index 参数 |
| 项目安装 | 缺失或冲突依赖 | 重新激活 `.venv`，执行 requirements 安装和 `pip check` |
| Launcher | 加载错误 Vane | 输出 `sys.executable`、`sys.prefix`、`vane.__file__` 和重装命令 |
| Launcher | 版本/API 不匹配 | 逐项输出 expected/actual，拒绝静默降级 |
| Qwen preflight | health 不通 | 指向独立服务指南的进程、端口、driver 和 OOM 排错 |
| Qwen preflight | 模型名不匹配 | 展示 `/v1/models` 返回值和要求的 served name |
| E2E | OCR/AI/合同/SQL 失败 | 明确失败且不发布伪成功输出 |

## 测试设计

### 快速测试

更新 `tests/fast/test_launcher.py`：

- 验证 distribution 名称为 `vane-ai`；
- 验证新五项运行时标识；
- 验证当前环境来源，而不是固定主目录；
- 保留每一个版本字段错误都被拒绝的参数化测试；
- 验证缺失 API、错误包路径和非 Python 3.12 的诊断。

更新 `tests/fast/test_release_shape.py`：

- README 包含新的两步安装命令；
- README 链接 `docs/local-qwen-service.zh.md`；
- README 包含新 launcher 命令；
- 项目文本不再包含开发者本机固定 Vane 环境路径；
- 本地 Qwen 指南包含固定模型 revision、vLLM 版本和三类 smoke test。

### 全新环境验收

从空的临时目录创建 CPython 3.12 venv，然后只执行 README 命令。验收证据必须包含：

```text
sys.executable -> 新虚拟环境
vane.__file__ -> 新虚拟环境 site-packages
duckdb.__file__ -> 新虚拟环境 site-packages
old Vane prefix in sys.path -> false
pip check -> No broken requirements found
```

### 真实 E2E

在本地 Qwen 服务三个 smoke test 通过后执行：

```bash
python scripts/run_demo.py
```

成功标准：

- 两张 PNG 都完成真实 OCR；
- 两张 PNG 都完成真实 Qwen 调用；
- stateless JSON contract UDF 真实执行；
- 八个 core relation 全部执行；
- `audit_findings.jsonl` 恰好 3 行；
- `audit_summary.jsonl` 恰好 1 行；
- summary 为 `review_required`；
- flagged expert 为 `EXP-001`；
- winner 为 `SUP-JW-001 -> SUP-ZJ-002`。

## 包元数据

当前仓库跟踪 `src/procurement_compliance_audit_sql_demo.egg-info/`。实施时在 README 和依赖更新后重新生成 metadata，并确认：

- `PKG-INFO` 不再出现旧本地路径或旧版本；
- `requires.txt` 包含固定版 `vane-ai` 和 `openai`；
- 生成内容与 `pyproject.toml`、README 一致。

## 官方参考

- [vLLM 0.25.1 NVIDIA GPU installation](https://docs.vllm.ai/en/v0.25.1/getting_started/installation/gpu/)
- [vLLM 0.25.1 supported models](https://docs.vllm.ai/en/v0.25.1/models/supported_models/)
- [vLLM 0.25.1 OpenAI-compatible server](https://docs.vllm.ai/en/v0.25.1/serving/online_serving/openai_compatible_server/)
- [vLLM 0.25.1 security guidance](https://docs.vllm.ai/en/v0.25.1/usage/security/)
- [Qwen2.5-VL-3B-Instruct model card](https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct)
- [Hugging Face `hf download` CLI](https://huggingface.co/docs/huggingface_hub/en/package_reference/cli)

## 完成定义

只有同时满足以下条件才算完成：

1. README 和本地 Qwen 指南能由没有开发者本机环境的新用户逐步执行；
2. 依赖元数据完整声明 Vane 和 OpenAI client；
3. launcher 不包含任何开发者绝对路径并只使用当前环境；
4. 快速测试全通过；
5. 全新虚拟环境安装检查全通过；
6. 本地模型服务 smoke test 全通过；
7. 真实 E2E 输出完全符合 fixture 合同；
8. 项目文本扫描不到旧 Vane 环境路径；
9. Git diff 不包含模型权重、虚拟环境、缓存或 E2E 输出。
