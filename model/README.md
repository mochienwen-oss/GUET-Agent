# 检索模型与 BM25 词表

生成回答、规划和 PPT 内容使用 `deploy/llm-api.json` 中配置的 API 模型；知识库检索仍需要本地 embedding 和 reranker 两个模型。

| 模型 | 用途 | 下载来源 | 默认目录 |
| --- | --- | --- | --- |
| Embedding | 把问题和文档转成向量，参与语义召回 | [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3) | `model/embedding_m3/` |
| Reranker | 给召回的候选资料重新打分排序 | [BAAI/bge-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3) | `model/rerank_m3/` |

`bm25_vocab.json` 是关键词检索词表，不能代替 embedding 模型。模型权重不提交到 GitHub，首次配置时需要下载；已经存在且完整的模型目录可以直接复用。

## Windows PowerShell 下载

在仓库根目录执行。先完成根 README 中的 Python 虚拟环境和依赖安装；以下命令明确使用项目的 `.venv`。

```powershell
.\.venv\Scripts\python.exe -m pip install huggingface_hub
.\.venv\Scripts\python.exe -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='BAAI/bge-m3', local_dir='model/embedding_m3', ignore_patterns=['onnx/*', 'imgs/*', 'long.jpg'])"
.\.venv\Scripts\python.exe -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='BAAI/bge-reranker-v2-m3', local_dir='model/rerank_m3', ignore_patterns=['assets/*'])"
```

两个模型的权重都在 GB 量级。Embedding 下载保留 `pytorch_model.bin`、分词器、配置和 `1_Pooling/`，排除本项目不使用的 ONNX 副本；reranker 保留 `model.safetensors`、配置和分词器。下载实现使用官方 [snapshot_download](https://huggingface.co/docs/huggingface_hub/guides/download)，中断后可再次运行同一命令使用已有缓存。

激活虚拟环境后，Git Bash 或 Linux 也可执行 `bash scripts/models/download_bge.sh` 下载到相同目录。

## 后端配置

在 `.env` 中设置：

```dotenv
EMBED_MODEL=model/embedding_m3
RERANK_MODEL=model/rerank_m3
EMBED_DEVICE=cpu
RERANK_DEVICE=cpu
RERANK_FP16=0
```

相对路径要求服务从仓库根目录启动；使用其他工作目录时填写模型目录的绝对路径。

需要 GPU 时，先安装支持本机 CUDA 环境的 PyTorch，再改为：

```dotenv
EMBED_DEVICE=cuda:0
RERANK_DEVICE=cuda:0
RERANK_FP16=1
```

复用现有向量库时必须使用与建库时相同的 embedding 模型；即使向量维度相同，换模型后也不能混用旧向量。更换 embedding 模型需要另行重建索引。BM25 词表也应与知识库及稀疏索引配套，不要用另一份资料的词表覆盖正在使用的词表。
