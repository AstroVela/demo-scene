# 基金投研 Demo 运行手册

## 1. 运行边界

本 Demo 只支持 Ray Runner，并且强制使用 `~/vane` 下的本地 Vane 构建。Launcher 会校验：

- CPython 3.11；
- `vane-ai==0.1.0a1` 必须从 `~/vane/dist` 的本地 wheel 安装；
- Vane API、DuckDB Python、DuckDB engine 和 source revision 的精确标识；
- `vane.ai.prompt` 必须支持 `image_columns`；
- Vane 必须提供 Ray Runner。

不要绕过 `scripts/run_demo.py` 直接调用包内 CLI，否则会跳过这些运行时身份检查。

## 2. 系统依赖

需要 Linux x86_64、glibc 2.28 或更新版本，以及：

```bash
python3 --version
pdftoppm -v
ffmpeg -version
espeak --version
```

Fixture 使用 eSpeak/pyttsx3 生成无真人数据的合成英文录音，再由 ffmpeg 转为 16 kHz 单声道 WAV。PDF 页面通过 `pdftoppm` 转为 OCR 图片。

## 3. 创建环境并安装本地 Vane

在 `fund-investment-research` 目录执行：

```bash
uv venv --python ~/vane/.venv/bin/python .venv
uv pip install \
  --python .venv/bin/python \
  ~/vane/dist/vane_ai-0.1.0a1-cp311-cp311-linux_x86_64.whl
uv pip install --python .venv/bin/python -e '.[test]'
```

确认来源：

```bash
.venv/bin/python -c \
  "from importlib import metadata; print(metadata.distribution('vane-ai').read_text('direct_url.json'))"
```

输出中的 `file:` 路径必须位于 `~/vane`。

## 4. 启动依赖服务

默认 [`runtime.yml`](../runtime.yml) 期望：

| 服务 | 地址 | 健康检查要求 |
| --- | --- | --- |
| PostgreSQL | `127.0.0.1:5432` | DSN 可连接 |
| MinIO | `127.0.0.1:9000` | 凭据可创建/读写 Demo bucket |
| Qwen2.5-VL | `http://127.0.0.1:8001/v1` | `/health` 返回 `{"status":"ok","model":"Qwen2.5-VL-3B-Instruct"}` |
| Whisper | `http://127.0.0.1:8002/v1` | `/health` 返回 `{"status":"ok","model":"whisper-small"}` |

Qwen 服务可参考仓库根目录的[本地 Qwen 服务指南](../../docs/local-qwen-service.zh.md)。Whisper 服务需要实现 OpenAI-compatible `audio/transcriptions` 接口并返回文本、起止时间、语言和可选置信度。Fixture 音频固定为 16 kHz 单声道。

检查：

```bash
curl -fsS http://127.0.0.1:8001/health
curl -fsS http://127.0.0.1:8002/health
```

默认 `ray.address` 为空，由 Vane 启动本机 Ray。若连接现有 Ray 集群，所有 Worker 必须能够访问 PostgreSQL、MinIO、模型服务和 Runner 使用的共享临时文件系统。

`runtime.yml` 中是本地 Demo 凭据，不应复制到生产环境。Launcher 会清除 HTTP/SOCKS proxy 变量，并为 loopback 服务设置 `NO_PROXY`，避免 Ray Worker 把本地模型请求发送到代理。

## 5. 快速测试

```bash
.venv/bin/pytest -q tests/fast
```

快速测试不调用真实模型，覆盖严格配置、JSON 合同、领域纠错、图片列传递、角色语义合同和确定性 SQL。真实 Ray、ASR、OCR、Qwen、PostgreSQL 和 MinIO 由 E2E 验收。

## 6. 默认 E2E

```bash
.venv/bin/python scripts/run_demo.py e2e
```

等价的分步命令：

```bash
.venv/bin/python scripts/run_demo.py fixture --scenario default
.venv/bin/python scripts/run_demo.py run
.venv/bin/python scripts/run_demo.py verify --scenario default
```

`fixture` 是唯一读取/生成本地 seed 的阶段。`run` 只从 PostgreSQL 和 MinIO 读取正式输入。

成功快照：

```text
output/default/current/
```

常用检查：

```bash
sed -n '1,220p' output/default/current/signal_evidence_report.md
rg 'SIG-CLINICAL|SUBGROUP_ORR' \
  output/default/current/research_signals.jsonl \
  output/default/current/thesis_impact_edges.jsonl
rg 'original_span|canonical_term|knowledge_status' \
  output/default/current/asr_corrections.jsonl \
  output/default/current/transcript_segments.jsonl
```

## 7. 词表数据变更演示

先生成不包含 `actin-4 → Nectin-4` 别名的快照：

```bash
.venv/bin/python scripts/run_demo.py fixture --scenario glossary-before
.venv/bin/python scripts/run_demo.py run
.venv/bin/python scripts/run_demo.py verify --scenario glossary-before
```

再只改变 PostgreSQL `domain_terms` 数据：

```bash
.venv/bin/python scripts/run_demo.py fixture --scenario glossary-after
.venv/bin/python scripts/run_demo.py run
.venv/bin/python scripts/run_demo.py verify --scenario glossary-after
```

最后一条验收会同时确认：

- 变更前目标术语未修正，知识状态为 `review_required`；
- 变更后产生 `TERM-TARGET-001` 纠错，知识状态为 `accepted`；
- 前后 `pipeline_sha256` 相同。

## 8. 真实坏输入与恢复演示

装载一个真实损坏的临床 PDF：

```bash
.venv/bin/python scripts/run_demo.py fixture --scenario recovery-fault
.venv/bin/python scripts/run_demo.py run
```

第二条命令必须非零退出，错误包含 `SRC-CLINICAL` 的哈希或 PDF 门禁原因；不能发布不完整的 `output/recovery/current`，但其他来源已成功的阶段状态保留在 PostgreSQL。

修复同一个对象并恢复：

```bash
.venv/bin/python scripts/run_demo.py fixture --scenario recovery-fixed
.venv/bin/python scripts/run_demo.py run --resume
.venv/bin/python scripts/run_demo.py verify --scenario recovery-fixed
```

验收器要求 manifest 中：

```text
resume = true
resume_recomputed_source_ids = ["SRC-CLINICAL"]
```

即恢复只选择变化或失败的临床来源阶段，未变化来源复用已通过合同的结果。

## 9. 故障语义

- 单一来源 locator、哈希、媒体或解码失败：写入 `quarantined`，整次快照不发布；
- OCR 低质量或失败：写入来源级失败，整次快照不发布；
- AI 返回非唯一 JSON、错误字段、缺失必需事实/影响三元组：严格重试一次，仍失败则退出；
- PostgreSQL、MinIO、Ray、ASR、Qwen、SQL 或发布失败：系统级非零退出；
- 发布前合同失败：不切换 `current`，最近一次成功快照保留。

## 10. 常见问题

`local Vane runtime mismatch`
: 删除 `.venv` 后按第 3 节重新用 `~/vane` 的 CPython 和 wheel 安装。

`local vane.ai.prompt does not expose image_columns`
: 当前安装不是用户指定的图片版本地 Vane。

`Ray worker cannot reach 127.0.0.1`
: 确认模型服务监听地址、Worker 网络命名空间和 proxy 设置；远程集群不能把 Driver 的 loopback 当作服务地址。

`AI_CONTRACT`
: 错误会包含最后一次合成模型响应的截断诊断。不要放宽整个 JSON 合同，应修正 Prompt 的具体结构或角色语义。

`Vector::Reference ... BOOLEAN referenced VARCHAR`
: 本 Demo 的规则 SQL 已使用显式窄 Arrow 输入和 `SUM/COUNT(CASE ...)`，避开本地 Vane FTE 对 aggregate `FILTER` 的已知兼容问题。

不要提交 `.venv/`、`output/`、模型权重、真实资料或生产凭据。
