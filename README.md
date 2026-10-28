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
| `backend/` | FastAPI 后端、Agent、检索、文档处理、权限与测试 |
| `backend/agent/skills/ppt_master/` | 已接入的 PPT Master Skill 及其原始许可 |
| `frontend/` | Vue 3、TypeScript 和 Vite 前端 |
| `final-md/` | 已整理的知识库 Markdown 原文和配图 |
| `backend/data/` | 文档清单、课程与专业结构化资料、引用关联等必要数据 |
| `model/` | 模型下载说明及 BM25 词表；权重需自行下载，见 [model/README.md](model/README.md) |
| `deploy/` | API 模型配置、环境变量示例和部署文件 |
| `scripts/` | 本地启动、知识导入及资料维护入口 |

## 本地运行（Windows PowerShell）

当前本地已使用的环境为 Windows、Python 3.13.5、Node.js 24.14.1。以下以 Windows 为主，所有命令均在仓库根目录执行；涉及实际安装、模型下载和知识库导入的命令由操作者按需执行。

如果这台电脑已经有完整环境和 `backend/.env`，直接看“日常再次启动”，不要覆盖现有密钥配置、重新下载已有模型或重复导入知识库。

### 1. 获取代码并创建 Python 环境

```powershell
git clone https://github.com/mochienwen-oss/guet-agent.git
cd guet-agent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m pip install -r backend/agent/skills/ppt_master/requirements.txt
```

私有仓库需要先登录有访问权限的 GitHub 账号。这里直接调用虚拟环境中的 Python，无需修改 PowerShell 的脚本执行策略。

Windows 的 PPT 页面预览需要已安装并可正常使用的桌面版 Microsoft PowerPoint；`pywin32` 由后端依赖安装。仅安装 Python 和 `python-pptx` 不能代替 PowerPoint 预览引擎。

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

下载细节、设备配置和模型目录说明见 [model/README.md](model/README.md)。仓库中的 BM25 词表不能代替 embedding 权重；使用 API 生成回答也不意味着知识库检索不需要本地模型。已有完整模型目录时直接复用。

### 3. 准备 MySQL

已有 MySQL 时，确认 `guet_chat` 数据库存在，并在下一步填写真实连接地址、端口和密码。

全新环境若使用 Docker Desktop，可用以下示例创建独立 MySQL。先将示例密码 `change-this-password` 替换成自己的密码；下一步的数据库连接必须使用同一个密码。已有数据库或 33306 端口已被使用时跳过此命令，沿用现有数据库。

```powershell
docker run -d --name guet-agent-mysql -e MYSQL_ROOT_PASSWORD=change-this-password -e MYSQL_DATABASE=guet_chat -p 127.0.0.1:33306:3306 -v guet-agent-mysql-data:/var/lib/mysql mysql:8.4
```

示例使用本机 33306 端口映射容器 3306 端口。这里仅用 Docker 运行 MySQL；后端、前端构建和模型仍在本机运行。

首次创建容器后，等待 MySQL 初始化完成再继续；可通过 `docker logs guet-agent-mysql` 查看是否已出现 `ready for connections`。

### 4. 配置后端环境变量和 API

仅在没有私有配置文件时复制示例：

```powershell
if (-not (Test-Path backend/.env)) { Copy-Item backend/.env.example backend/.env }
```

编辑 `backend/.env`，至少确认：

```dotenv
MYSQL_URL=mysql+pymysql://root:change-this-password@127.0.0.1:33306/guet_chat?charset=utf8mb4
ADMIN_USERNAME=admin
ADMIN_PASSWORD=请设置自己的管理员密码
DASHSCOPE_API_KEY=填写自己的API密钥

QDRANT_PATH=qdrant_local
QDRANT_COLLECTION=structure_docs_20261019
ACTIVE_DOCUMENTS_PATH=run/documents.local.json
BM25_VOCAB_PATH=model/bm25_vocab.json

EMBED_MODEL=model/embedding_m3
EMBED_DEVICE=cpu
RERANK_MODEL=model/rerank_m3
RERANK_DEVICE=cpu
RERANK_FP16=0
```

- `deploy/llm-api.json` 决定生成模型、平台和参数。当前 `active_profile=aliyun`，读取 `DASHSCOPE_API_KEY`；选择其他 profile 时填写对应 `api_key_env` 指定的变量。
- `QDRANT_PATH` 非空时使用本地嵌入式 Qdrant，不需要另外启动 Qdrant 容器；如果改用 HTTP Qdrant 服务，应把 `QDRANT_PATH` 留空并正确设置 `QDRANT_URL`。
- CPU 配置便于先启动；使用 GPU 时先准备支持本机 CUDA 的 PyTorch，再按 [模型说明](model/README.md) 调整设备参数。
- 相对路径以仓库根目录为工作目录。恢复已有知识库时，MySQL、向量库、文档清单及 BM25 词表必须配套，`ACTIVE_DOCUMENTS_PATH` 应改为该数据集实际使用的清单。
- 仅启用 PPT 公开搜索时填写 `VOLC_SEARCH_API_KEY`；其余现有搜索配置见 `deploy/volc-search.env.example`，按需合并到 `backend/.env`。未配置或不满足现有启用条件时，搜索会如实显示不可用。

生成模型和公开搜索的实际调用可能产生平台费用。密钥只放在本地 `.env`，不要填写到 README 或示例文件。

### 5. 全新空库首次初始化（已有配套数据时跳过）

本仓库没有上传本机 MySQL 和 Qdrant 数据。第一次克隆到新机器，只有代码与资料原文，不能跳过知识库初始化就声称已经具备检索能力。

以下步骤仅用于全新空库：先完成模型下载、数据库与 `.env` 配置，确保 MySQL 可连接，后端服务尚未启动。嵌入式 Qdrant 不能被导入进程和服务进程同时占用。

先创建数据库表和管理员，再为本机新建空文档清单。清单使用独占创建，已有同名文件时会报错，不覆盖已有记录：

```powershell
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'backend'); from database.db import init_db, seed_admin; init_db(); seed_admin()"
.\.venv\Scripts\python.exe -c "from pathlib import Path; import json; p=Path('run/documents.local.json'); p.parent.mkdir(exist_ok=True); p.open('x', encoding='utf-8').write(json.dumps({'version':'active-documents/1','collection':'structure_docs_20261019','points':0,'documents':[]}))"
```

然后导入原文。此过程在本机生成向量与 BM25 数据，会占用 CPU 或 GPU，首次耗时取决于设备：

```powershell
$env:MD_DIR = (Resolve-Path final-md).Path
.\.venv\Scripts\python.exe scripts/seed_knowledge.py
Remove-Item Env:MD_DIR
```

确认导入日志中的失败和拒绝条目已处理，再继续启动。目录中的同名文件会按导入器的同标题更新方式处理，实际文档数和块数以导入日志及本机清单为准。

若是恢复原有数据库，应恢复与其匹配的向量存储、活动文档清单和 BM25 词表，跳过空库创建及批量导入。仓库中的 `backend/data/documents.active.json` 是已有数据集的记录，不能当作新建空库的导入结果。

### 6. 构建前端

需要先安装 Node.js。执行：

```powershell
cd frontend
npm.cmd ci
npm.cmd run build
cd ..
```

构建输出为 `frontend/dist/`，由后端同一端口托管。正常使用已构建页面时，不必另外启动 Vite 开发服务。

### 7. 启动和访问

```powershell
.\.venv\Scripts\python.exe scripts/local_api.py --host 127.0.0.1 --port 8080
```

看到 `Application startup complete` 后访问 <http://127.0.0.1:8080/>，使用 `.env` 中配置的管理员账号登录。健康接口为 <http://127.0.0.1:8080/api/health>；健康接口正常说明服务已启动，知识库是否可检索还取决于前面的模型与数据初始化。

在运行终端按 `Ctrl+C` 停止服务。

### 日常再次启动

数据库和知识库已经配置好后，只需要确认 MySQL 正在运行，再在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe scripts/local_api.py --host 127.0.0.1 --port 8080
```

若 MySQL 使用上面的 Docker 容器且已停止，可先运行 `docker start guet-agent-mysql`。Python 依赖、模型和知识库不需要每次重装或重建；前端源码变化后才需要重新构建。

## 当前多轮对话流程

1. 同一会话的消息保存到 MySQL。模型每轮读取最近 6 条聊天消息，约为最近 3 轮完整问答；当前新问题随后单独加入。
2. API 模型结合系统说明、这些历史消息和当前问题，决定直接回答，或调用 `search_regulations(query, keywords)`。跨轮的省略、指代及是否换话题由模型结合上下文判断。
3. 需要检索时，模型可以把追问组织成语义完整的 `query`，例如结合上轮“本科生转专业”，把“那研究生呢”组织为“研究生转专业的申请条件”。程序没有保证每次都自动补全正确。
4. 工具结果回到本轮模型循环，模型据此回答；无依据、不相关或查询失败的终止状态由后端按既定规则直接回复。最终回答入库，供后续对话读取。

`MULTITURN_CARRY` 是底层检索的兼容选项：只有调用方显式传入 `prev_query` 时，才会将上一轮问句的前 120 个字符拼到当前语义检索串前面。有 `keywords` 时 BM25 使用关键词，否则使用该检索串。当前普通聊天的模型工具不传 `prev_query`，所以这个开关不控制聊天记忆；设为 0 也不会停止模型读取历史。

配置注释中的“零额外 LLM 调用”只描述字符串拼接本身。正常对话仍调用 API，历史消息也会计入本轮输入。当前聊天窗口不是无限历史或永久记忆，上轮工具返回的完整资料不会自动作为下一轮工具结果重放。

## 数据与本地配置

仓库不附带 `.env` 密钥、MySQL 账号与聊天会话数据、Qdrant 数据目录、检索模型权重、用户 PPT 任务、安装依赖或构建产物。克隆代码后，需要另行配置或恢复运行数据，无法仅凭本仓库还原本机的数据库状态。

`docs/` 中的本地交接、审计和实施记录保留在本机，不再纳入当前仓库。运行必需的资料仍在 `backend/data/` 和 Skill 自身资源目录中；历史提交不因本次清理而改写。

已有本地数据库若缺少引用展示元数据，可单独执行 `scripts/backfill_source_metadata.py` 补提取，无需为此重建向量或 BM25。培养方案解析和名称归一化保留于 `backend/documents/plan_data/`，用于资料维护及实体识别，不作为模型可调用的业务工具。

## 第三方组件

PPT Master 的来源、版本与固定提交记录在 `backend/agent/skills/ppt_master/manifest.json`，其 MIT 许可保留于同目录的 `LICENSE`。其他依赖按各自许可使用。
