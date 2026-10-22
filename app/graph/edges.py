import json
from app.utils.logger import sys_logger
import app.graph.app_graph_config as app_graph_config
from langgraph.graph import END
from app.graph.state import EngineState


def start_router(state: EngineState) -> str:
    """
    允许通过初始状态注入 skip 标志跳过解析层
    """
    if state.get("skip_readers", False):
        sys_logger.info("跳过 所有Readers，直接进入 Analyzer。")
        return "analyzer_node"
    elif state.get("skip_reader02", False):
        sys_logger.info("跳过 Reader01 & 02，直达 Reader03。")
        return "human_intervention_node_02"
    elif state.get("skip_reader01", False):
        sys_logger.info("跳过 Reader01，直达 Reader02。")
        return "human_intervention_node"
    return "reader01_node"


def reader01_router(state: EngineState) -> str:
    """Reader01 段落自循环"""
    current_idx = state.get("reader01_current_index", 0)
    total_count = state.get("reader01_total_segments_count", 0)

    if current_idx < total_count:
        return "reader01_node"

    macro_config = app_graph_config.reader01_macro_config
    macro_instances = macro_config.get("instances", [])
    custom_props = macro_config.get("schema", {}).get("custom_properties", {})
    total_macro_tasks = len(macro_instances) * len(custom_props.keys()) if custom_props else 0

    if total_macro_tasks > 0:
        sys_logger.info(f"Reader01 提取完毕，转入 Macro 归纳 (共 {total_macro_tasks} 个子任务)...")
        return "reader01_macro_summarizer_node"

    sys_logger.info("Reader01 提取完毕，无归纳任务，直接进入人工清洗")
    return "human_intervention_node"


def reader01_macro_router(state: EngineState) -> str:
    """Macro 归纳自循环"""
    task_idx = state.get("reader01_macro_task_index", 0)

    macro_config = app_graph_config.reader01_macro_config
    macro_instances = macro_config.get("instances", [])
    custom_props = macro_config.get("schema", {}).get("custom_properties", {})
    total_macro_tasks = len(macro_instances) * len(custom_props.keys()) if custom_props else 0

    # 游标还没跑满任务数，继续转回自身节点处理下一个
    if task_idx < total_macro_tasks:
        return "reader01_macro_summarizer_node"

    sys_logger.info("Macro 完毕，进入人工清洗")
    return "human_intervention_node"


def human_intervention_node(state: EngineState) -> dict:
    """什么都不做，仅仅是为了把挂起动作挡在 Reader02 循环之外"""
    return {}


def human_intervention_node_02(state: EngineState) -> dict:
    """拦截在 Reader03 之前，模拟 Reader02 跑完后的审计节点"""
    return {}


def reader02_router(state: EngineState) -> str:
    """
    Reader02 自循环
    """
    current_idx = state.get("reader02_current_index", 0)

    # 动态获取总段数，保证脱离 Reader01 时依然独立运作
    with open(app_graph_config.reader01_node_read_segments_path, 'r', encoding='utf-8') as f:
        all_segments = json.load(f)

    # 为了测试阶段不全量分析，限制解析段数
    total_count = min(app_graph_config.reader02_router_test_segment_num, len(all_segments))

    if current_idx < total_count:
        return "reader02_state_node"

    sys_logger.info("Reader02 状态推演全部完毕，准备隔离等待审查")

    return "human_intervention_node_02"


def reader03_router(state: EngineState) -> str:
    """
        Reader03 自循环
        """
    current_idx = state.get("reader03_current_index", 0)
    with open(app_graph_config.reader01_node_read_segments_path, 'r', encoding='utf-8') as f:
        all_segments = json.load(f)

    total_count = min(app_graph_config.reader03_router_test_segment_num, len(all_segments))

    if current_idx < total_count:
        return "reader03_chunking_node"  # 还有段，继续循环

    sys_logger.info("Reader03 解析完成，移交 Analyzer。")
    return END
