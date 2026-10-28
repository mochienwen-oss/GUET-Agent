# config.py —— 全局配置：从环境变量 / .env 读取（环境变量 > 默认值）
import os

from core.paths import BACKEND_ROOT, PROJECT_ROOT

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# 本地模型目录优先（与部署时 EMBED_MODEL=/models/embedding 同一个思路）：
# 目录存在就不走 HF 下载。名字必须写**完整 HF id**（裸名会被 SentenceTransformer
# 补成 "sentence-transformers/bge-m3" → 404）。
_HERE = str(BACKEND_ROOT)
_LOCAL_EMBED = os.path.join(os.path.dirname(_HERE), "model", "embedding_m3")
_LOCAL_RERANK = os.path.join(os.path.dirname(_HERE), "model", "rerank_m3")


class Settings(BaseSettings):
    # 根目录 .env 使用绝对路径读取，与启动时的工作目录无关。
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # 所有生成任务必须使用统一 API；空配置不再选择本地模型。
    llm_api_config_path: str = os.path.join(os.path.dirname(_HERE), 'deploy', 'llm-api.json')
    system_prompt_path: str = os.path.join(_HERE, "prompts", "api-agent.txt")
    inference_threads: int = 4
    agent_request_timeout_seconds: float = 180.0
    index_upsert_batch_size: int = 64
    active_documents_path: str = os.path.join(_HERE, "data", "documents.active.json")
    # 旧材料打包辅助函数的字符上限，不限制当前 API 证据包。
    max_context_chars: int = 8000

    # ==== 阶段 2 的四个开关（每个独立、失败开放）====
    # 2a 流程章节完整召回：命中流程/程序章节时按 document_id 补齐同章节相邻块。
    #    关掉即回到「只给命中块」的旧行为。判据见 backend/flows.py（单一来源）。
    flow_expand: bool = True
    # 2b 跨文档引用跟随（一跳、≤2 份）。运行时**只读白名单**
    #    `backend/data/ref_allowlist.json`，不跑正则（正则只是生成器）。
    #    ⚠️ 白名单是**新文件，部署要一起拷**；拷不到自动关闭（失败开放）。
    ref_follow: bool = True
    # 底层兼容：显式提供 prev_query 才拼接；当前聊天依赖模型历史，不传该参数。
    #    关闭时忽略显式传入的 prev_query，不影响聊天历史窗口。
    multiturn_carry: bool = True

    # ==== 方案 1：sparse 质量门（默认**关**，失败开放）====
    # 背景（2026-10-08 实测 5 题）：RRF **只看名次**，不看该路返回了多少条、也不看分的
    # 绝对水平 —— 于是一条只返回 20~42 条、且**正解全不在**的 sparse 路，
    # 其第 1 名与 dense 的第 1 名**完全等权**，把 dense 的好名次拖差：
    #   题「四级 500」dense 第 3 → 融合第 6；「拟接收名单」dense 第 46 → 融合第 79；
    #   「材料成型→自动化」dense 第 89 → 融合第 185。
    # 且 sparse 是「**高置信度地错**」：top 分 8~14.4，top-3 文档却几乎全错
    # （有一题 top-3 是同一份《教室使用管理办法》）。
    # 判据：sparse 返回条数少 = 词面交集薄 = 该路不可信；同时 dense 有把握 = 值得独占。
    # ⚠️ 已知救不了两路皆✗ 的题（如「两把尺子」），那类留给方案 4（辅助查询）。
    sparse_fuse_gate: bool = False
    # 低于这个条数就算「词面交集薄」。实测触发区间 20~42，误触发区 300。
    sparse_min_n: int = 100
    # dense 最高分达到这个值才敢独占。实测这几题 dense 0.60~0.72。
    dense_conf: float = 0.60

    # ==== 方案 6：门控窄带软化（默认**关**，失败开放）====
    # 背景：门控对口语化提问约 20% 误判空，且实测 47 道**可答**题的 best 只差阈值 0.04。
    # 2026-10-08 实测 5 题：4 道的池内最高分落在 0.502~0.538，紧贴 rerank_low=0.54 之下。
    # 做法：**只在窄带内、且必须同时命中流程/场景意图**时提为 medium。
    # ⚠️ **绝不降全局 DEFAULT_RERANK_LOW** —— 那会把越界题一起放进来（带外零影响是它敢用的前提）。
    # 判据在 `backend/intent.py`（单一来源，与方案 4 共用）。
    gate_scenario_band: bool = False
    # 窄带宽度：best ∈ [low − 0.03, low) 才考虑。0.03 取自「47 道只差 0.04」的实测量级。
    gate_band_width: float = 0.03
    # 判据口径（**2026-10-08 实测三者的差异，供人核后择一**）：
    #   "flow_and_scenario"（默认，用户收紧要求）：流程词 ∧ 第一人称 ∧ 业务实体
    #        → 318 提级 **0 题**、越界误提级 0 → **完全 no-op**：
    #          用户在「自述情况」时通常不问「怎么办」，两项很少同现；
    #          8 题探针里那 2 道干净救回的（六级 480、挂了好几门）正是 flow=False。
    #   "scenario"：只要求 第一人称 ∧ 业务实体
    #        → 越界题绝大多数**没有第一人称**（清华/北大/文号类），天然被排除；
    #          是「既救得回、又不易误伤」的候选，**待 A/B 实测**。
    #   "flow_or_scenario"：任一命中（最松）
    gate_band_mode: str = "flow_and_scenario"

    # ==== 方案 4：意图识别 + 辅助查询多路召回（默认**关**，失败开放）====
    # 背景：口语化的对照型问题，dense 与 sparse **两路都召不到**正确校规
    #   （实测「两把尺子」两路皆✗；「拟接收名单」dense 46 / sparse ✗）。
    #   办法：按**人核过的路由登记表**（`backend/data/route_templates.json`）
    #   追加一条「规范化制度查询」，用**语料自己的措辞**去召。
    # ⚠️ 路由模板是**数据不是代码** —— 读不到就**不路由**（退回主路）。
    # ⚠️ 未命中任何意图的查询走**纯主路**（与旧行为逐字相同）。
    aux_route: bool = False
    # aux 路召回条数上限。**刻意远小于主路 120**：辅助路若也泛滥，
    #   会变成第三个培养方案票仓，反而把正解挤掉。30 是安全边际，A/B 要验名次分布。
    aux_recall_n: int = 30
    # 一次最多路由几个族（多族同现时**全捞**但上限 2，不做唯一猜测；超出的记日志留痕）。
    aux_max_topics: int = 2
    # 按主题覆盖 aux_recall_n，如 {"转专业": 50}。读不到用上面的全局值。
    aux_recall_n_by_topic: dict = {}

    # ==== 精排严格模式（**评测专用**，默认关；生产必须保持失败开放）====
    # 背景（2026-10-08 实测事故）：`rag.retrieve` 里精排失败是 `except Exception` **静默降级**
    #   到 embedding 判档 —— 结果照常产出、指标悄悄变差。一次 636 次检索的 A/B 中精排降级，
    #   同一 318 题集算出 R@5 95.91%（真值 99.37%）、low 6 道（真值 1 道），**统计被污染**。
    #   与「rerank_m3 只传了一半 → 生产静默无精排」同一类病。
    # 处置：① 降级**一定打 warning 日志**（生产可见）；② 本开关为真时**直接抛出**，
    #   让评测任务显式中止，禁止降级结果流入统计。
    # ⚠️ 生产部署**不要开**（铁律 12：新开关读不到要退回旧行为，而不是让服务挂掉）。
    rerank_strict: bool = False

    # ==== 多信号放行（默认**关**，失败开放；依赖方案 4 的 aux_route）====
    # 背景（2026-10-08 实测）：33 道判空题的 best **全部落在 0.502~0.542**，
    #   距 `rerank_low=0.54` 只差最多 0.038；而 aux 路已把正解送进池、31/33 排第 1。
    #   判空纯属**阈值卡在正上方**，不是「库里没有」。
    # 信号（运行时**看不到哪块是正解**，所以用可观测代理）：
    #   开关 on ∧ 强专名命中（topics 非空）∧ **aux 路聚焦到同一份文档** → 放行。
    # ⚠️ 三道硬门槛照样要过：24 越界零误放、扩展压力零误触发、318 R@1/MRR 零回退。
    gate_multi_signal: bool = False
    # 看 aux 路前多少块、同一 document_id 要出现多少次才算「聚焦」。
    aux_focus_topn: int = 5
    aux_focus_need: int = 3
    # 放行时**只对聚焦文档**把每文档上限放宽到这里（其余文档仍 `max_chunks_per_doc=2`）。
    # 起因：实测 A28/A29 放行了但正解不在窗 —— 聚焦文档的块被 2 块/文档挤掉。
    # ⚠️ **全局 `max_chunks_per_doc` 一个字节都不动**：只有 multi_signal 这条路径调
    #    `_cap_per_doc_focus`，所以带外零影响、318 基线逐位可复现。
    multi_signal_focus_cap: int = 4

    # Agent 总预算：限制执行成本，不因旧训练的单轮形态截断调用。
    agent_max_steps: int = 4                   # 进 `agent_decide` 的次数上限
    agent_max_tool_calls: int = 6              # 整轮问答执行工具调用的**总量**上限

    # ==== 阶段3：Agent 图（主图 + PPT 子图）====
    # ★ 下列预算**全是估计值**（阶段0.5 §5.3），首版按它跑，**真机实测后再定** ——
    #   不要把它们当成调优过的数。
    # ★ 循环只允许两处：`research ⇄ evidence_check` 与 `qa → author` 的限次修复。
    agent_ppt_max_tool_calls: int = 20         # 整个 PPT 任务最多几次工具调用
    agent_ppt_timeout_sec: int = 1800         # 单段墙钟预算；超时**只存进度，不自动交付**
    # PPT 进度落盘目录（相对 backend/）。**用文件** —— 不引入新服务、不动 MySQL。
    agent_ppt_workdir: str = "data/ppt_tasks"
    agent_ppt_token_ttl_sec: int = 600
    agent_ppt_task_ttl_sec: int = 86400

    # 独立豆包搜索 Custom API；不使用方舟生成服务或 AK/SK。
    volc_search_api_key: SecretStr = Field(default_factory=lambda: SecretStr(''), repr=False)
    # 只有核实平台会在免费额度用尽后停止、不会转付费，才可设置为真。
    # 下面是账号实际剩余额度的已核实窗口，不按宣传额度或自然月自动重置。
    volc_search_free_only_verified: bool = False
    volc_search_free_window_start: str = ''
    volc_search_free_window_end: str = ''
    volc_search_free_budget: int = Field(default=0, ge=0)
    volc_search_usage_path: str = os.path.join(os.path.dirname(_HERE), 'run', 'volc_search_usage.json')

    # Qdrant 向量库
    qdrant_url: str = "http://localhost:6333"
    # 本地验证可直接打开向量存储；留空时继续连接 HTTP 服务。
    qdrant_path: str = ""
    qdrant_collection: str = "school_docs"
    # ★ BM25 词表路径（阶段5A 裁决 三.5）。词表是全语料统计量（idf/avgdl），
    #   与语料一一对应 —— 所以**集合名和词表必须成对切换**：
    #     QDRANT_COLLECTION=school_docs_phase5 BM25_VOCAB_PATH=model/bm25_vocab_phase5.json
    #   留空 = 沿用项目级默认 `model/bm25_vocab.json`（即引入本配置前的行为，逐字不变）。
    #   生产默认仍是空 —— 不设就不会动到旧集合在用的那份词表。
    bm25_vocab_path: str = ""

    # Embedding 模型
    # ★ bge-m3 而不是 bge-large-zh-v1.5：后者 max_position_embeddings=512，
    #   800 字的块在**向量化阶段就会被静默截断**，块尾部根本不参与检索。
    #   bge-m3 是 8192 token，装得下 chunk_max_len；且自带稀疏（词权重）输出，
    #   多路召回那条路零额外模型。
    #   ⚠️ 换模型后必须**重建 collection**（旧向量与新向量不同源，混在一起检索全废）：
    #      rm -rf qdrant_local  然后重跑 scripts/knowledge/ingest_local.py
    embed_model: str = _LOCAL_EMBED if os.path.isdir(_LOCAL_EMBED) else "BAAI/bge-m3"
    # query 侧指令前缀。**bge-m3 不需要**（官方用法是无指令检索），留空即可。
    # ⚠️ 若换回 bge-large-zh-v1.5 之类的模型，必须改回
    #    "为这个句子生成表示以用于检索相关文章："，否则检索质量明显下降——
    #    这类模型的 convention 是「query 加指令、doc 不加」。
    query_prefix: str = ""
    # ★ rerank 的**文档侧输入是否带《标题》+ 章节归属**。
    #   实测 65 份培养方案是同一套模板，同学分要求的块逐字几乎相同，只喂原文时
    #   reranker 分不出「这块来自哪份文档」，只能照单接收 RRF 带偏的顺序
    #   （ext 集 5 道表格题全被《智慧交通专业培养方案》抢走）。
    #   留成开关是为了让 A/B 可复现——用 import 时序去区分「改前/改后」不可靠。
    rerank_include_title: bool = True
    # ★ embedding 的运行设备。必须显式设成 cpu：
    #   ① **线上**（主因）：GPU 归宿主机的 vllm serve（`--gpu-memory-utilization 0.95`），
    #      embedder 上去就是抢显存。`RERANK_DEVICE` 早就有这道保护，embedding 侧原先没有——
    #      容器里恰好没 GPU 所以没暴露，但宿主机原生 systemd 那条路上 bge-m3 会自己抢 CUDA。
    #      与 CLAUDE.md 硬约束 6 是同一件事。
    #   ② **本地**：bge-m3 要同时装载 dense + sparse 两份（sparse head 是单独的
    #      `sparse_linear.pt`，ST 会转成独立的 SparseEncoder 实例）。本机只有 15.2G 内存，
    #      两份模型 + CUDA context 时实测 RSS 到 7.4G（未见失败，但余量很小）。
    #   ⚠️ 排查注记：入库存档长期停在 "Converting ... to SparseEncoder." 看似卡死，
    #      实际是**重定向到文件后 stdout 变成块缓冲**、✅ 还没被 flush 出来——
    #      判断进度要看 `qdrant_local/collection/*/storage.sqlite` 是否在长大，别只看日志。
    #   本地想快可 EMBED_DEVICE=auto（前提内存够、且没有 vLLM 在同一张卡上）。
    embed_device: str = "cpu"
    # 编码批大小。默认 32 是 sentence-transformers 的缺省值，但那是给**短句**设的：
    # 现在的块长中位 737 字（≈700+ token），32×800 token 的前向激活在 8G 笔记本卡上
    # 会把显存压到边界——实测入库到最大的那份文档（38.7k 字 / 64 块）时，dense 编码
    # 在 XLM-RoBERTa forward 里长时间不返回（栈显示在**算**，不是死锁），整个入库卡住。
    # 降到 8 之后每批激活降约 4 倍，代价是略慢一点，换来稳定跑完。
    embed_batch_size: int = 8

    # Reranker（cross-encoder 精排）模型；rerank_device: auto=cuda 优先，可设 "cuda"/"cpu"
    # ★ bge-reranker-v2-m3 而不是 bge-reranker-base：后者的 max_position_embeddings
    #   只有 514，**装不下 400~800 的块**——250 字块时碰不到，换了块长立刻越界，
    #   CPU 上静默退回 embedding 判档、CUDA 上直接把 CUDA context 打坏。
    #   换模型后**门槛阈值必须重标**（sigmoid 分数分布会整体平移，见 rerank_high/low）。
    rerank_model: str = _LOCAL_RERANK if os.path.isdir(_LOCAL_RERANK) else "BAAI/bge-reranker-v2-m3"
    rerank_device: str = "auto"
    # ★ 精排用 fp16 装载。**上 GPU 时必须有**：24G 卡上 vLLM 占 0.85（≈21.5G）后
    #   只剩 ~2.6G，而 bge-reranker-v2-m3 fp32 权重 2.27G + forward 激活会 OOM；
    #   fp16 把权重压到 1.13G，留足激活余量。
    #   ⚠️ **分数不受影响**：2026-10-02 实测 6 条跨区间的题，GPU/fp16 与 CPU/fp32
    #   的 best 分**逐位相同**（Δ=0.0000），所以 rerank_high/low 的标定（0.72/0.555）
    #   不用重标。换模型/换 dtype 后仍应重跑这个对照（`_work/v4/parity` 那类脚本）。
    rerank_fp16: bool = False

    # MySQL
    mysql_url: str = "mysql+pymysql://root:password@localhost:3306/guet_chat?charset=utf8mb4"

    # 管理员种子账号
    admin_username: str = "admin"
    admin_password: str = "admin123"

    # RAG 检索条数
    top_k: int = 5
    # 精排/评估前召回的小块候选数（只影响 rag.py 的 expand_limit，扩大召回池避免漏召回）
    # ★ 这是**每路**（dense / sparse）的原始条数，不是最终池子大小。
    #   2026-10-03 实测：65 份同模板培养方案占了全库 3393 个向量里的 **3116（92%）**，
    #   20 条一路根本召不到被它们挡住的规章制度文档（实测名次 93/73/33/15）。
    #   同语料 318 题 A/B：20 → 120 使 R@1 97.5% → 98.4%、未命中 5 → 2（零回退）。
    #   A/B 时用环境变量 RECALL_POOL=20 回退到旧行为。
    recall_pool: int = 120
    # ★ 精排池上限：深召回后两路并集可能上千条，而精排是**一次 forward 全部 pairs**
    #   —— 24G 卡上 vLLM 已占 21.5G，实测一次精排 120 条直接 CUDA OOM。
    #   40 与今天的池子同规模（dense 20 + sparse 20 的并集上限），成本不变。
    rerank_pool: int = 40
    # ★ 跨文档**近似**块折叠阈值（字符 5-gram Jaccard）。0 = 关闭。
    #   见 rag._dedupe_near 的实测：65 份培养方案的同一段模板文字逐字不相等、
    #   所以 _dedupe_by_text（逐字去重）抓不到，只能靠近似判据折叠。
    #   同语料 318 题 A/B：0.6 让 R@1 97.5% → 98.4%（3 题由未命中升到第 1，0 回退）；
    #   0.3 的结果与 0.6 **逐项相同**（R@1/未命中都一样），既然更强折叠换不来收益，
    #   就取更保守的 0.6。代价：检索 0.32s → 0.58s/次。
    #   A/B 时用环境变量 NEAR_DUP_THRESHOLD=0 回退到旧行为。
    near_dup_threshold: float = 0.6
    # ★ 混合召回：dense（语义）+ sparse（词权重，抓专有名词/文号）双路，RRF 融合。
    #   为什么需要：单路向量对「桂电教〔2025〕28号」「博士后」这类**精确词面**是弱项；
    #   而 65 份同模板培养方案在向量空间里天然容易被广泛查询命中（它们没有文号，
    #   sparse 路会把它们按下去）。关掉它可做 A/B（USE_SPARSE=0）。
    use_sparse: bool = True
    # RRF 融合的平滑常数：score = Σ 1/(k + rank)。60 是原论文的经验值，
    # 越小则头部名次越占主导。
    rrf_k: int = 60
    # 同一份文档最多占几个上下文槽位（0 = 不限）。
    # 实测「重复占用槽位 53.4%」——一份文档的多个相邻块挤满 top-k，模型拿到的是
    # 同一段内容的几个副本。设 2 让 5 个槽位至少覆盖 3 份文档。
    # 用 eval_retrieval.py 的「重复占用槽位」「整窗只有 1 份文档的题」两个指标判断效果。
    max_chunks_per_doc: int = 2

    # ★ 递归切片参数（改动**必须重建索引**才生效）
    # 取代原 small-to-big 的 chunk_small_* / chunk_parent_*：
    # 400~800 的块本身就是合格的生成单元，不再需要「小块检索 + 父块生成」两层。
    chunk_min_len: int = 400         # 非条款块下限：短于此的并入相邻块
    chunk_target: int = 600          # 累积目标：到了就开始找切点
    chunk_max_len: int = 800         # 硬上限
    # ★ 只在**字符硬切**时生效；自然断点处 0% 重叠。
    #   原实现（chunk_small_overlap=50 无条件叠加）会造出一批近重复块互相抢分——
    #   断点本身就是完整边界，再叠重叠纯属自伤（见 vector._cut_long_paragraph 的注释）。
    chunk_overlap_ratio: float = 0.2


settings = Settings()
