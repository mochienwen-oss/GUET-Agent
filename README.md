# GUET-Agent

面向桂林电子科技大学学生的校园问答与聊天式 PPT 制作工作区。

第一版：`v1.0.0`，2026-10-28。此版本保存当前本地已实现的项目代码和知识资料。

## 当前功能

- 校园资料检索问答：API 模型规划与工具调用，结合向量检索、BM25 和精排；检索无依据、不相关或失败时返回明确状态。
- 答案引用交互：点击引用阅读原文及上下文，定位段落或表格，查看按原文依据提取的日期、适用对象和适用范围。
- 聊天式 PPT 工作区：持续对话、材料登记、大纲与页面预览、后台制作、暂停续做、修订和 PPTX 下载。
- PPT 公开资料搜索：接入火山引擎独立搜索 API；密钥由后端环境变量提供。
- 用户登录、会话保存和知识库管理。


## 目录

| 路径 | 内容 |
| --- | --- |
| `requirements.txt` | 后端 Python 依赖 |
| `.env.example` | 根目录环境变量示例；复制为私有 `.env` 后配置 |
| `backend/` | FastAPI 后端、Agent、检索、文档处理、权限与测试；文档解析和资料目录分别位于 `documents/ingest/`、`documents/catalog/` |
| `backend/agent/skills/ppt_master/` | 已接入的 PPT Master Skill 及其原始许可 |
| `frontend/` | Vue 3、TypeScript 和 Vite 前端；组件按通用、布局、聊天、演示文稿和管理分类 |
| `desktop/` | Electron 桌面客户端；`campus/` 为校园页面导航和接口模块 |
| `final-md/` | 已整理的知识库 Markdown 原文；`assets/` 配图仅在本地保留 |
| `backend/data/` | 文档清单、课程与专业结构化资料、引用关联等必要数据 |
| `model/` | 模型下载说明及 BM25 词表；权重需自行下载，见 [model/README.md](model/README.md) |
| `deploy/` | API 模型配置、环境变量示例和部署文件 |
| `scripts/` | 按启动、知识库、文档处理、检查、评测和模型下载分类的脚本 |

### 主要源码层级

- `backend/agent/runtime/`：Agent 运行、上下文、短期和长期记忆。
- `backend/agent/tools/search_regulations/scripts/`：`querying/` 查询理解、`recall/` 召回与排序、`index_ops/` 索引、`evidence_ops/` 证据及 `inspection/` 诊断。
- `backend/agent/tools/create_ppt/scripts/`：`execution/` 任务流程、`master/` PPT Master 宿主、`content/` 材料内容及 `rendering/` 页面预览与导出。
- `backend/documents/`：`ingest/` 解析与 OCR、`catalog/` 资料目录与元数据、`plan_data/` 培养方案处理。
- `frontend/src/components/`：`common/` 通用组件、`layout/` 布局、`chat/` 对话、`presentation/` 演示文稿及 `admin/` 管理后台。
- `desktop/campus/`：学校页面导航、校园接口和调试采集模块。

## 本地运行（Windows PowerShell）

环境：Windows、Python 3.13、Node.js、本地 PostgreSQL（含 pgvector）和 Redis。PPT 页面预览还需要桌面版 Microsoft PowerPoint。

如果这台电脑已经有完整环境和 `.env`，直接看“日常再次启动”，不要覆盖现有密钥配置、重新下载已有模型或重复导入知识库。

### 1. 获取代码并创建 Python 环境

```powershell
git clone --depth 1 https://github.com/mochienwen-oss/GUET-Agent.git
cd GUET-Agent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r backend/agent/skills/ppt_master/requirements.txt
```

以下命令在仓库根目录执行；私有仓库需要有访问权限的 GitHub 账号。

### 2. 下载 embedding 和 reranker 模型

两个本地检索模型都要准备：

| 模型 | 用途 | 本地目录 |
| --- | --- | --- |
| `BAAI/bge-m3` | 问题、文档向量化，参与语义召回 | `model/embedding_m3/` |
| `BAAI/bge-reranker-v2-m3` | 给召回资料重新排序 | `model/rerank_m3/` |

```powershell
.\.venv\Scripts\python.exe -m pip install huggingface_hub
.\.venv\Scripts\python.exe -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='BAAI/bge-m3', local_dir='model/embedding_m3', ignore_patterns=['onnx/*', 'imgs/*', 'long.jpg'])"
.\.venv\Scripts\python.exe -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='BAAI/bge-reranker-v2-m3', local_dir='model/rerank_m3', ignore_patterns=['assets/*'])"
```

已有完整模型目录时直接复用。CPU/GPU 配置见 [model/README.md](model/README.md)。

### 3. 启动本地数据服务

项目当前使用 PostgreSQL（含 pgvector）和 Redis。已有本机数据时，保留现有数据库、向量索引及配套的 BM25 词表，不要创建空库覆盖它们。

```powershell
.\scripts\runtime\start_local_data.ps1
```

脚本使用已准备的本地服务程序及 `run/` 下的数据目录。新环境需要先配置 PostgreSQL、pgvector、Redis 和相应数据。

### 4. 配置后端和 API

仅在没有私有配置文件时复制配置示例；已有 `.env` 不应覆盖。确认 `DATABASE_URL`、`REDIS_URL`、模型及管理员配置指向本机实际环境。

- `deploy/llm-api.json` 决定生成模型、平台和参数，密钥放在私有 `.env` 中。
- PPT 公开搜索配置见 `deploy/volc-search.env.example`。
- 已有知识库的文档清单、向量数据和 BM25 词表必须相互配套。

生成模型和公开搜索的实际调用可能产生平台费用。不要将密钥写入版本库。

### 5. 首次部署与数据恢复

全新环境需准备 PostgreSQL、pgvector、Redis、检索模型和知识库数据。旧 MySQL / Qdrant 数据的迁移脚本位于 `scripts/knowledge/`；已有配套数据时不应重复迁移或重建索引。

### 6. 构建前端

需要先安装 Node.js。执行：

```powershell
cd frontend
npm.cmd ci
npm.cmd run build
cd ..
```

前端构建完成后由后端统一托管。

### 7. 启动和访问

```powershell
.\.venv\Scripts\python.exe scripts/runtime/local_api.py --host 127.0.0.1 --port 8080
```

看到 `Application startup complete` 后访问 <http://127.0.0.1:8080/>，使用 `.env` 中配置的管理员账号登录。按 `Ctrl+C` 停止服务。

### 日常再次启动

确认本地 PostgreSQL 和 Redis 服务正在运行，执行上面的后端启动命令即可。无需重复安装依赖、下载模型或导入知识库；前端代码变动后才需重新构建。

## 数据与本地配置

仓库不包含私有 `.env`、PostgreSQL 数据、模型权重和用户 PPT 任务，新环境需自行初始化或恢复配套数据。原文配图 `final-md/assets/` 也需另行恢复。

## 第三方组件

PPT Master 的来源、版本与固定提交记录在 `backend/agent/skills/ppt_master/manifest.json`，其 MIT 许可保留于同目录的 `LICENSE`。其他依赖按各自许可使用。
