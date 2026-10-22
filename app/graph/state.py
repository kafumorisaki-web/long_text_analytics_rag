from typing import TypedDict, List, Dict, Any, Annotated
from langgraph.graph.message import add_messages
from langchain_core.messages import AnyMessage


# 全局state:业务数据
class EngineState(TypedDict, total=False):
    # 控制跳过某个node
    skip_reader01: bool
    skip_reader02: bool
    skip_readers: bool

    """
    reader01
    """
    # 当前执行控制参数
    current_segment_num: int

    # 前一段落的事实梗概，用来解决本段的指代消解
    reader01_previous_segment_summary: str

    # 待处理文本分段数量
    reader01_total_segments_count: int

    # 当前处理到第几个分段的索引
    reader01_current_index: int

    # 一个归纳节点的脉络节点是否已全部解析
    reader01_skeleton_built: bool

    # 取代 reader01_macro_summarized 布尔值，改为更精准的步进器
    reader01_macro_task_index: int

    """
    reader02
    """
    reader02_current_index: int

    """
    reader03
    """
    reader03_current_index: int

    """
    analyzer
    """
    messages: Annotated[list[AnyMessage], add_messages]
    # 控制大循环游标
    analyzer_cursor: int
