import os
import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph, START, END
from app.graph.state import EngineState
from app.graph.nodes import (
    reader01_node,
    reader01_macro_summarizer_node,
    reader02_state_evaluator_node,
    reader02_relation_evaluator_node,
    reader02_neo4j_writer_node,
    reader03_chunking_node,
    analyzer_node
)
from langgraph.prebuilt import ToolNode
from app.graph.edges import (
    start_router,
    reader01_router,
    reader01_macro_router,
    human_intervention_node,
    human_intervention_node_02,
    reader02_router,
    reader03_router
)
import app.graph.app_graph_config as app_graph_config
from app.utils.logger import sys_logger

sys_logger.info("初始化langgraph workflow")
workflow = StateGraph(EngineState)

"""大图部分"""
# 注册节点
workflow.add_node("human_intervention_node", human_intervention_node)
workflow.add_node("reader01_node", reader01_node)
workflow.add_node("reader01_macro_summarizer_node", reader01_macro_summarizer_node)
workflow.add_node("reader02_state_node", reader02_state_evaluator_node)
workflow.add_node("reader02_relation_node", reader02_relation_evaluator_node)
workflow.add_node("reader02_writer_node", reader02_neo4j_writer_node)
workflow.add_node("human_intervention_node_02", human_intervention_node_02)
workflow.add_node("reader03_chunking_node", reader03_chunking_node)
workflow.add_node("analyzer_node", analyzer_node)

workflow.add_conditional_edges(START, start_router)
# [Reader01 层] -> 自循环或移交
workflow.add_conditional_edges("reader01_node", reader01_router)
# 为归纳车间添加自循环的条件边
workflow.add_conditional_edges("reader01_macro_summarizer_node", reader01_macro_router)
# [隔离舱 1] -> Reader02
workflow.add_edge("human_intervention_node", "reader02_state_node")
# [Reader02 层] -> 串行压榨流 (状态提完提关系，利用前缀缓存)
workflow.add_edge("reader02_state_node", "reader02_relation_node")
workflow.add_edge("reader02_relation_node", "reader02_writer_node")
# 通过 reader02_router 决定是循环还是流向 human_intervention_node_02
workflow.add_conditional_edges("reader02_writer_node", reader02_router)
# [隔离舱 2] -> Reader03
workflow.add_edge("human_intervention_node_02", "reader03_chunking_node")
# [Reader03 层]
workflow.add_conditional_edges("reader03_chunking_node", reader03_router)
# 临时设定：Analyzer 跑完后直接指向 END，防止当前无终止条件导致无限死循环回 Reader01
# 最终架构应当是 reader03 -> analyzer -> reader01 的死循环，
# 但在 Analyzer 尚未具备“拉取新文本、重置 reader01_current_index”的能力前，
# 强行连回 reader01_node 会导致原有的切片数据被无限重复读取宕机。
# 因此这里先用 END 锚定，等 MVP2 加上调度能力后，只需要把 END 改成 reader01_node 即可。
workflow.add_edge("analyzer_node", END)

# 物理初始化本地 SQLite 存储，它会在项目目录下自动建一个持久化数据库文件
db_path = app_graph_config.graph_sqlite_db_path
os.makedirs(os.path.dirname(db_path), exist_ok=True)
conn = sqlite3.connect(db_path, check_same_thread=False)
checkpointer = SqliteSaver(conn)

# 编译图，生成可执行实例
# 每个节点每次执行完毕并return后，完整的全局state、当前节点、任务thread_id均打包交给checkpointer，由其写入到对应的本地sqlite数据库
compiled_graph = workflow.compile(
    checkpointer=checkpointer,
    interrupt_before=[
        "human_intervention_node",
        "human_intervention_node_02"
    ]
)

sys_logger.info("生成compiled_graph")
