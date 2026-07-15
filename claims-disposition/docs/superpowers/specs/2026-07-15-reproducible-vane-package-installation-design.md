# Vane 理赔分流 Demo 可复现安装设计

日期：2026-07-15
状态：已确认

## 目标

把当前依赖开发者专用 Vane 解释器的运行方式，迁移为任何满足已验证平台要求的用户都可以从源码仓库独立完成的安装流程。

新用户应当能够：

1. 创建项目自己的 Python 3.12 虚拟环境；
2. 从 TestPyPI 安装固定版 `vane-ai`；
3. 从项目元数据安装全部 Python 直接依赖；
4. 准备 PostgreSQL、MinIO 和本地 OpenAI-compatible Qwen 服务；
5. 使用当前环境执行 `python scripts/run_demo.py e2e`；
6. 得到并验证四条预期理赔分流结果。

文档、依赖元数据、launcher 和测试必须表达同一个安装合同，不得依赖开发者主目录、预装 site-packages 或未声明的 Python 包。

## 已确认的方案

采用与招采审计 Demo 一致的“项目环境 + 外部服务”边界：

- 项目 `.venv` 安装 Vane wheel、当前源码、PostgreSQL/MinIO client、OCR、OpenAI client、Arrow 和测试依赖；
- PostgreSQL 和 MinIO 作为本机数据服务运行；
- Qwen/vLLM 使用单独的模型服务环境，避免 PyTorch、CUDA、vLLM 和项目 Vane/Ray 二进制依赖互相污染；
- launcher 只验证当前 `sys.executable` 和 `sys.prefix` 中的固定版运行时，不再拼接另一个虚拟环境；
- 公开入口改名为 `scripts/run_demo.py`，不保留旧 launcher；
- 默认 E2E 继续执行真实 OCR、真实 Qwen 调用和真实 PostgreSQL/MinIO I/O，不增加 mock fallback。

## 范围

### 包含

- 重写英文和中文 README 的平台、安装、服务准备、运行、验证与排错流程；
- 增加独立的中英文 Qwen2.5-VL 本地服务指南；
- 明确 PostgreSQL、MinIO 的本地服务合同、凭据边界和连通性检查；
- 在 `pyproject.toml` 中声明固定版 `vane-ai`、`openai==2.45.0` 和测试依赖；
- 把 `requirements.txt` 改为标准 editable 项目安装入口；
- 把 launcher 改为只使用当前 Python 环境，并精确校验 Vane/DuckDB 标识；
- 新增 launcher 和发布安装合同测试；
- 从空虚拟环境执行安装、快速测试、服务探测和真实 E2E。

### 不包含

- 修改业务 SQL、OCR、AI prompt 或理赔决策规则；
- 自动启动、停止或重启现有 PostgreSQL、MinIO、Qwen 服务；
- 把模型权重、虚拟环境、数据库文件或运行结果提交到仓库；
- Windows、macOS、ARM64、ROCm、Intel XPU 或 CPU-only 模型服务的发布承诺；
- 公网部署、TLS、多用户鉴权、备份和生产级服务运维。

## 已验证平台合同

### 项目环境

| 组件 | 固定或已验证值 |
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

TestPyPI wheel 只对 CPython 3.12、Linux x86_64、`manylinux_2_39` 做发布验收。README 可以说明其他平台未验证，但不能暗示它们已受支持。

### 数据服务合同

默认 `runtime.yml` 使用只面向本地 Demo 的配置：

- PostgreSQL：`127.0.0.1:5432/vane_insight`；
- MinIO：`127.0.0.1:9000`，bucket `claims-disposition-fixtures`；
- launcher 不负责创建服务进程；
- `fixture` 负责创建所需 schema/table/bucket，并刷新合成测试数据；
- 文档明确默认凭据只能用于 loopback Demo，不可直接用于生产。

### 模型服务合同

模型服务沿用已验证的招采 Demo 合同：

| 组件 | 指南值 |
| --- | --- |
| Python | CPython 3.12，独立虚拟环境 |
| vLLM | `vllm==0.25.1` |
| 模型 | `Qwen/Qwen2.5-VL-3B-Instruct` |
| 模型 revision | `66285546d2b821cf421d4f5eb2576359d3770cd3` |
| served model name | `Qwen2.5-VL-3B-Instruct` |
| bind address | `127.0.0.1:8001` |
| API key | `dummy` |
| 最大上下文 | 4096 tokens |
| 单请求图片上限 | 1 |

指南必须包含 `/health`、`/v1/models` 和真实图片 `/v1/chat/completions` 三类检查，并强调只绑定 loopback。

## 安装流程

README 的项目安装命令固定为：

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

采用两步安装，使 Vane 的 TestPyPI 来源和精确版本对用户可见，同时让其普通依赖继续从 PyPI 解析。`pyproject.toml` 仍声明同一固定版 Vane，使项目元数据真实；第二步会复用第一步已经安装的 wheel。

`requirements.txt` 只保留：

```text
# Install the pinned TestPyPI Vane wheel first; see README.md.
-e .[test]
```

## Python 依赖

运行依赖由 `pyproject.toml` 统一声明：

- `vane-ai==0.1.0.dev20260714234347`：Vane API、定制 DuckDB 和 worker runtime；
- `openai==2.45.0`：`provider: openai` 的本地 Qwen client；
- `minio`：fixture 和 UDF 的对象存储访问；
- `psycopg[binary]`：PostgreSQL 输入与原子结果发布；
- `rapidocr`、`onnxruntime`：CPU stateful OCR actor；
- `numpy`、`pillow`：图片生成、读取与质量分析；
- `pyarrow`：Vane relation 与 Python 边界；
- `pyyaml`：严格加载 `runtime.yml`；
- `pytz`：合成 fixture 的时区稳定性；
- `pytest`：快速安装合同测试，仅放在 `test` extra。

不再把任何依赖描述为旧 Vane runtime 的 supplemental package。

## Launcher 设计

新的 `scripts/run_demo.py` 保留四个命令：

```text
fixture | run | verify | e2e
```

launcher 在导入项目代码前执行以下 fail-fast 检查：

1. Python 主次版本必须为 3.12；
2. distribution 名称 `vane-ai` 的版本必须精确匹配；
3. `vane.__version__`、DuckDB Python、engine 和 source revision 必须精确匹配；
4. `vane.func`、`vane.cls`、`vane.attach_function`、`vane.configure`、`vane.ai.prompt` 必须可调用；
5. `duckdb.ray_cxx` 必须存在；
6. `vane.__file__` 和 `duckdb.__file__` 必须位于当前 `sys.prefix`；
7. 错误信息必须输出当前解释器、prefix、expected/actual 和可复制的安装命令。

保留 loopback AI URL 的代理清理逻辑。删除固定开发者 prefix、旧 site-packages、supplemental `.venv` 拼接、`site.addsitedir` 和 worker `PYTHONPATH` 手工覆盖。editable 安装和当前解释器负责让主进程与 Vane worker 使用同一环境。

## 文档结构

### `README.md` 与 `README.zh-CN.md`

两份 README 使用相同信息架构：

1. Demo 价值与预期四类结果；
2. 已验证平台；
3. 从零创建项目环境并安装固定 Vane；
4. PostgreSQL、MinIO、Qwen 服务合同与 preflight；
5. `fixture`、`run`、`verify`、`e2e` 命令；
6. 依赖用途；
7. 数据流、Vane 特性和业务边界；
8. 安装与运行排错；
9. 精确运行时标识。

已有业务、SQL DAG、数据合同和隐私内容继续保留，但所有开发者本机路径和 supplemental runtime 叙述都删除。

### Qwen 服务指南

- `docs/local-qwen-service.md`：英文；
- `docs/local-qwen-service.zh.md`：中文。

两份指南包含系统/GPU/磁盘/端口检查、独立 vLLM 环境、固定模型 revision 下载、loopback 启动命令、三类 smoke test、停止/重启方式，以及 driver、CUDA OOM、模型名、端口、权重和代理排错。

## 错误处理

| 阶段 | 失败 | 用户提示 |
| --- | --- | --- |
| Vane 安装 | 找不到 matching distribution | 检查 Ubuntu x86_64、Python 3.12 和两个 index 参数 |
| 项目安装 | 依赖缺失或冲突 | 激活 `.venv`，重跑 requirements 和 `pip check` |
| Launcher | 错误解释器或包来源 | 展示解释器、prefix、包文件和固定安装命令 |
| PostgreSQL/MinIO | 服务不可达 | 展示服务名称和 endpoint，不泄露 secret |
| Qwen | health/model/图片请求失败 | 指向独立模型服务指南 |
| E2E | OCR、AI JSON、SQL 或发布失败 | 非零退出，不发布伪成功结果 |

## 测试设计

### Launcher 测试

新增 `tests/fast/test_launcher.py`，验证：

- 当前固定版环境可通过完整 runtime 检查；
- Vane 与 DuckDB 都来自当前 `sys.prefix`；
- launcher 不再暴露固定开发者 prefix 或 worker path 拼接；
- 五个 runtime 标识任一不匹配都会被拒绝；
- Python 版本、缺失 API、错误包来源和缺少 `ray_cxx` 有明确诊断；
- `scripts` 中唯一 Python 入口是 `run_demo.py`。

### 发布安装合同测试

新增 `tests/fast/test_release_shape.py`，验证：

- 中英文 README 都包含虚拟环境、TestPyPI、extra index、固定 Vane、requirements、四个命令和服务指南；
- `pyproject.toml` 声明固定 Vane、固定 OpenAI client 与 test extra；
- `requirements.txt` 是标准 editable 安装入口；
- Qwen 指南固定 vLLM、model revision 和三类 smoke test；
- 发布文本和代码中不再存在旧开发者 runtime 路径。

测试按 TDD 执行：先新增契约测试并观察其因旧 launcher/旧文档而失败，再做最小实现使其通过。

## 全新环境验收

在一个新的 `/tmp` 虚拟环境中只执行 README 的安装命令，验收证据必须包含：

```text
sys.executable -> 新环境
vane.__file__ -> 新环境 site-packages
duckdb.__file__ -> 新环境 site-packages
legacy developer prefix in sys.path -> false
pip check -> No broken requirements found
```

不启动或重启现有服务，只做连通性检查，然后运行：

```bash
python -m pytest tests/fast -q
python scripts/run_demo.py e2e
python scripts/run_demo.py verify
```

E2E 成功标准：

- 加载 4 条 claim 和 8 个 MinIO object；
- 完成真实 RapidOCR 与 Qwen 多模态调用；
- 发布 4 条 PostgreSQL disposition；
- `CLM-APPROVE=approve_for_payment`；
- `CLM-DENY=deny_claim`；
- `CLM-MISSING=request_more_materials`；
- `CLM-REVIEW=manual_review`。

## 完成定义

只有同时满足以下条件才算完成：

- 两份 README 与两份 Qwen 指南可供新用户复制执行；
- 依赖元数据和 requirements 与文档一致；
- launcher 只使用当前虚拟环境并精确验证运行时；
- 契约测试经历 RED → GREEN 且全部通过；
- 全新虚拟环境的 `pip check`、快速测试、真实 E2E 和独立 verify 全部通过；
- Git diff 中没有业务规则变化、虚拟环境文件或运行产物。
