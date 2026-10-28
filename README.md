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
| `backend/` | FastAPI 后端、Agent、检索、文档处理、权限与测试 |
| `backend/agent/skills/ppt_master/` | 已接入的 PPT Master Skill 及其原始许可 |
| `frontend/` | Vue 3、TypeScript 和 Vite 前端 |
| `final-md/` | 已整理的知识库 Markdown 原文；`assets/` 配图仅在本地保留 |
| `backend/data/` | 文档清单、课程与专业结构化资料、引用关联等必要数据 |
| `model/` | 模型下载说明及 BM25 词表；权重需自行下载，见 [model/README.md](model/README.md) |
| `deploy/` | API 模型配置、环境变量示例和部署文件 |
| `scripts/` | 按启动、知识库、文档处理、检查、评测和模型下载分类的脚本 |

## 本地运行（Windows PowerShell）

环境：Windows、Python 3.13、Node.js 24、本地 MySQL。PPT 页面预览还需要桌面版 Microsoft PowerPoint。

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

### 3. 启动本地 MySQL

安装 MySQL 并注册为 Windows 服务。以管理员身份打开 PowerShell，查看并启动服务：

```powershell
Get-Service *mysql*
Start-Service MySQL84
```

`MySQL84` 替换为本机实际服务名，也可以在 Windows“服务”中启动。服务已运行时跳过启动。

使用 MySQL 命令行创建数据库，输入安装时设置的密码：

```powershell
mysql -h 127.0.0.1 -P 33306 -u root -p -e "CREATE DATABASE IF NOT EXISTS guet_chat DEFAULT CHARACTER SET utf8mb4;"
```

示例端口为 `33306`；若本机使用 `3306` 或其他端口，修改命令中的端口，并在下一步填写相同的连接地址。若终端找不到 `mysql`，将 MySQL 安装目录下的 `bin` 加入 PATH。

### 4. 配置后端环境变量和 API

仅在没有私有配置文件时复制示例：

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

编辑 `.env`，至少确认：

```dotenv
MYSQL_URL=mysql+pymysql://root:change-this-password@127.0.0.1:33306/guet_chat?charset=utf8mb4
ADMIN_USERNAME=admin
ADMIN_PASSWORD=请设置自己的管理员密码
DASHSCOPE_API_KEY=填写自己的API密钥
```

- `deploy/llm-api.json` 决定生成模型、平台和参数。当前 `active_profile=aliyun`，读取 `DASHSCOPE_API_KEY`；选择其他 profile 时填写对应 `api_key_env` 指定的变量。
- 示例已配置本地模型路径和嵌入式 Qdrant，无需另外启动 Qdrant 服务。恢复已有知识库时，修改为与数据配套的存储、文档清单及 BM25 词表路径。
- PPT 公开搜索配置见 `deploy/volc-search.env.example`，按需将密钥和启用配置填入 `.env`。

生成模型和公开搜索的实际调用可能产生平台费用。密钥只放在本地 `.env`，不要填写到 README 或示例文件。

### 5. 全新空库首次初始化（已有配套数据时跳过）

仅用于全新空库。完成模型下载和 `.env` 配置，确认 MySQL 正在运行，后端尚未启动，再创建表、管理员和空文档清单：

```powershell
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'backend'); from database.db import init_db, seed_admin; init_db(); seed_admin()"
.\.venv\Scripts\python.exe -c "from pathlib import Path; import json; p=Path('run/documents.local.json'); p.parent.mkdir(exist_ok=True); p.open('x', encoding='utf-8').write(json.dumps({'version':'active-documents/1','collection':'structure_docs_20261019','points':0,'documents':[]}))"
```

导入原文，生成本机向量及 BM25 数据：

```powershell
$env:MD_DIR = (Resolve-Path final-md).Path
.\.venv\Scripts\python.exe scripts/knowledge/seed_knowledge.py
Remove-Item Env:MD_DIR
```

确认导入成功后再启动服务。恢复已有知识库时，使用配套的 MySQL、向量库、活动文档清单和 BM25 词表，跳过本节；不要用已有清单作为新空库的导入结果。

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

确认本地 MySQL 服务正在运行，执行上面的后端启动命令即可。无需重复安装依赖、下载模型或导入知识库；前端代码变动后才需重新构建。

## 数据与本地配置

仓库不包含私有 `.env`、MySQL 数据、Qdrant 存储、模型权重和用户 PPT 任务，新环境需自行初始化或恢复配套数据。原文配图 `final-md/assets/` 也需另行恢复。

## 第三方组件

PPT Master 的来源、版本与固定提交记录在 `backend/agent/skills/ppt_master/manifest.json`，其 MIT 许可保留于同目录的 `LICENSE`。其他依赖按各自许可使用。
