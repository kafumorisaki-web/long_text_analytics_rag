import app.agents.app_agents_config as app_agents_config
import app.graph.app_graph_config as app_graph_config
import root_running_config as root_running_config

"""qdrant_services.py"""
embedding_reader03_model = app_agents_config.embedding_reader03_model

# 相当于关系型数据库里的“表名”。Qdrant 接收到写入指令时，会先去查有没有这个名字的集合。如果没有，它就会在本地映射的 qdrant_storage 文件夹里，开辟一块物理空间，专门用来存 Reader03 切出来的所有文本块和元数据。
init_collection_name = "reader03_semantic_chunks"
# 取决于给 LLMClient 配置的嵌入模型。比如 text-embedding-3-small，它在底层的作用，就是把一段自然语言文本，通过神经网络压缩成一个包含绝对固定数量的浮点数数组。这个数组的长度，正好就是 1536 个数字。
init_vector_size = 1536

load_clean_segment_titles_clean_ledger_path = root_running_config.run_pass1_pipeline_clean_ledger

ingest_chunks_ledger_reader03_chunking_node_ledger_path = app_graph_config.reader03_chunking_node_ledger_path
ingest_chunks_ledger_meta_components = app_graph_config.reader03_meta_vector_components
ingest_chunks_ledger_custom_payload_fields = app_graph_config.reader03_custom_payload_fields
