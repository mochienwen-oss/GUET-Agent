# guet-agent

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
| `model/bm25_vocab*.json` | 已保存的 BM25 词表，不含模型权重 |
| `deploy/` | API 模型配置、环境变量示例和部署文件 |
| `scripts/` | 本地启动、知识导入及资料维护入口 |
| `docs/` | 实施记录与设计说明；历史文档可能描述旧架构，当前实现以代码为准 |

## 本地运行

1. 准备 Python、Node.js、MySQL，以及可用的检索模型和 Qdrant 数据环境。Windows PPT 预览使用的 PowerPoint/COM 等能力还需要对应本地程序。
2. 安装后端及 PPT Master 所需依赖：

   ```powershell
   python -m pip install -r backend/requirements.txt
   python -m pip install -r backend/agent/skills/ppt_master/requirements.txt
   ```

3. 复制 `backend/.env.example` 为 `backend/.env`，填写自己的数据库连接、管理员密码、模型路径与设备、Qdrant 连接或路径。默认示例密码应替换。
4. 在 `deploy/llm-api.json` 选择生成模型。此版本默认配置为阿里云兼容接口，读取 `DASHSCOPE_API_KEY`；其他平台使用各自 `api_key_env` 指定的环境变量。火山搜索使用 `VOLC_SEARCH_API_KEY`，说明见 `docs/PPT公开搜索接入说明.md`。
5. 构建前端：

   ```powershell
   cd frontend
   npm ci
   npm run build
   cd ..
   ```

6. 启动本地服务：

   ```powershell
   python scripts/local_api.py --host 127.0.0.1 --port 8080
   ```

访问 <http://127.0.0.1:8080/>。生成模型和公开搜索的实际调用可能产生平台费用。

## 数据与本地配置

仓库不附带 `.env` 密钥、MySQL 账号与聊天会话数据、Qdrant 数据目录、检索模型权重、用户 PPT 任务、安装依赖或构建产物。克隆代码后，需要另行配置或恢复运行数据，无法仅凭本仓库还原本机的数据库状态。

知识库初始化入口为 `scripts/seed_knowledge.py`；导入前确认目标 MySQL/Qdrant 与检索模型配置，并将 `MD_DIR` 指向所需资料目录，例如 `final-md/`。文档的展示元数据可通过 `scripts/backfill_source_metadata.py` 在本地补提取，无需为此重建向量或 BM25。

培养方案解析和名称归一化保留于 `backend/documents/plan_data/`，用于资料维护及实体识别，不作为模型可调用的业务工具。

旧资料解析脚本 `scripts/prepare_knowledge.py` 的可选 MinerU 凭据使用 `MINERU_API_TOKEN`，从环境变量或本地 `backend/.env` 读取。

## 第三方组件

PPT Master 的来源、版本与固定提交记录在 `backend/agent/skills/ppt_master/manifest.json`，其 MIT 许可保留于同目录的 `LICENSE`。其他依赖按各自许可使用。
