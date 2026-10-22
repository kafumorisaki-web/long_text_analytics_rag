import json
import atexit
from typing import Optional, List, Literal, Dict, Any
from pydantic import BaseModel, Field, create_model
from langchain_core.tools import tool
from app.database.neo4j_client import Neo4jClient
from app.database.qdrant_services import Reader03QdrantService
from app.utils.logger import sys_logger
from enum import StrEnum as Enum
import app.agents.app_agents_config as app_agents_config

from types import GenericAlias

_global_neo4j_client = None
_global_qdrant_service = None

def get_neo4j_client() -> Neo4jClient:
    global _global_neo4j_client
    if _global_neo4j_client is None:
        _global_neo4j_client = Neo4jClient()
        sys_logger.info("已初始化 Neo4j 全局单例连接池。")
    return _global_neo4j_client


def get_qdrant_service() -> Reader03QdrantService:
    global _global_qdrant_service
    if _global_qdrant_service is None:
        _global_qdrant_service = Reader03QdrantService()
        sys_logger.info("已初始化 Qdrant 全局单例连接池。")
    return _global_qdrant_service


@atexit.register
def _cleanup_connections():
    global _global_neo4j_client, _global_qdrant_service
    if _global_neo4j_client:
        _global_neo4j_client.close()
        _global_neo4j_client = None
    if _global_qdrant_service:
        _global_qdrant_service.close()
        _global_qdrant_service = None
    sys_logger.info("已释放所有数据库全局连接。")



def _apply_python_slice(results: list, slice_str: str) -> list:
    """ Python 风格切片"""
    if not slice_str or not results:
        return results
    try:
        if ":" not in slice_str:
            idx = int(slice_str)
            return [results[idx]] if -len(results) <= idx < len(results) else []

        parts = slice_str.split(":")
        start = int(parts[0]) if parts[0] else None
        end = int(parts[1]) if parts[1] else None
        step = int(parts[2]) if len(parts) > 2 and parts[2] else None
        return results[start:end:step]
    except Exception as e:
        sys_logger.warning(f"切片解析失败 '{slice_str}': {e}，将返回全量结果。")
        return results


def _resolve_bounds(client: Neo4jClient, level: str, start_id: Optional[int], end_id: Optional[int]) -> tuple[int, int]:
    """解析真实 ID 边界，处理 null 与负数倒数"""
    # LLM 如果显式传了 null，强制打回默认值
    start_id = 1 if start_id is None else start_id
    end_id = -1 if end_id is None else end_id

    label = "Macro" if level == "Macro" else "Segment"
    id_field = "macro_id" if level == "Macro" else "segment_id"

    bounds_cypher = f"MATCH (n:{label}) RETURN max(n.{id_field}) AS max_id"
    bounds_record = client.execute_read(bounds_cypher)

    max_id = bounds_record[0]["max_id"] if (bounds_record and bounds_record[0]["max_id"] is not None) else 0

    # 如果是负数，直接用 max_id 倒推
    actual_start = max_id + 1 + start_id if start_id < 0 else start_id
    actual_end = max_id + 1 + end_id if end_id < 0 else end_id

    return actual_start, actual_end


# 底层 Cypher 在返回结果前，全部强制执行了基于物理时间轴的升序排序 (ASC)。这保证了返回的全集是一条完美的时间线，切片语法的语义绝对可靠
# 大模型在查库时，结构性的指令 (Enum) 就全是英文，而事实性的搜索词 (Query) 就全是中文。
# ==========================================
# 工具1： 宏中观时空探测器
# ==========================================
def _get_narrative_context_input_schema():
    # 在系统启动时，从全局配置动态抽取出允许查询的属性白名单
    _macro_custom_props = [k for d in app_agents_config.macro_custom_props.values() if isinstance(d, dict) for k in
                           d.keys()]
    _segment_custom_props = list(app_agents_config.segment_custom_props.keys())

    # 准备基础字段字典，显式声明类型为 Dict[str, Any] 防止外溢警告
    fields: Dict[str, Any] = {
        "level": (Literal["Macro", "Segment"], Field(..., description="查询层级：Macro(归纳层) 或 Segment(脉络切片层)")),
        "start_id": (Optional[int],
                     Field(default=1, description="起始 Macro ID 或 Segment ID。负数代表倒数。若不传默认从开头开始。")),
        "end_id": (Optional[int],
                   Field(default=-1, description="结束 Macro ID 或 Segment ID。负数代表倒数。若不传默认到最后。")),
        "type": (Optional[str], Field(default="原文", description="所属分支，默认为'原文'")),
        "result_slice": (Optional[str],
                         Field(default=None, description="Python 风格的切片语法，用以筛出查询结果全集中的部分结果。"))
    }

    # 动态组装 Macro 属性白名单锁
    if _macro_custom_props:
        MacroAttrEnum = Enum("MacroAttrEnum", {k: k for k in _macro_custom_props})
        # 把类型声明为 Any 蒙蔽 IDE，运行时用 | None 拼接 Optional
        macro_list_type: Any = GenericAlias(list, (MacroAttrEnum,))
        fields["macro_attribute_keys"] = (macro_list_type | None, Field(default=None,
                                                                        description=f"仅当 level='Macro' 时，指定返回更多的归纳属性字段。可选值: {_macro_custom_props}。若不传则仅返回主键、名称和跨度区间等基础信息。"))
    else:
        fields["macro_attribute_keys"] = (Optional[List[str]], Field(default=None,
                                                                     description="仅当 level='Macro' 时，指定返回更多的归纳属性字段。若不传则仅返回基础信息。"))

    # 动态组装 Segment 属性白名单
    if _segment_custom_props:
        SegmentAttrEnum = Enum("SegmentAttrEnum", {k: k for k in _segment_custom_props})
        segment_list_type: Any = GenericAlias(list, (SegmentAttrEnum,))
        fields["segment_attribute_keys"] = (segment_list_type | None, Field(default=None,
                                                                            description=f"仅当 level='Segment' 时，指定返回更多的脉络属性字段。可选值: {_segment_custom_props}。若不传则仅返回主键、名称和所属归纳节点等基础信息。"))
    else:
        fields["segment_attribute_keys"] = (Optional[List[str]], Field(default=None,
                                                                       description="仅当 level='Segment' 时，指定返回更多的脉络属性字段。若不传则仅返回基础信息。"))

    return create_model("NarrativeContextInput", **fields)


# 生成动态 Pydantic 类交由 LangChain 解析
NarrativeContextInput = _get_narrative_context_input_schema()


@tool("query_macro_and_segment_context", args_schema=NarrativeContextInput)
def query_macro_and_segment_context(
        level: str,
        start_id: Optional[int] = 1,
        end_id: Optional[int] = -1,
        type: Optional[str] = "原文",
        result_slice: Optional[str] = None,
        macro_attribute_keys: Optional[List[str]] = None,
        segment_attribute_keys: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    宏中观时空探测器，从图数据库中查询原文段落属性。通过 level 切换大小节点模式
    """
    client = get_neo4j_client()
    actual_start, actual_end = _resolve_bounds(client, level, start_id, end_id)

    attribute_keys = macro_attribute_keys if level == "Macro" else segment_attribute_keys
    type = "原文" if type is None else type

    # 依据 Level 执行深度查询
    if level == "Macro":
        # 向下穿透寻找包含的 Segment 区间
        mac_cypher = """
            MATCH (m:Macro {type: $type})
            WHERE m.macro_id >= $start AND m.macro_id <= $end
            OPTIONAL MATCH (m)-[:CONTAINS]->(s:Segment)
            WITH m, min(s.segment_id) AS start_seg, max(s.segment_id) AS end_seg
            ORDER BY m.macro_id ASC
            RETURN m, start_seg, end_seg
            """
        records = client.execute_read(mac_cypher, {"type": type, "start": actual_start, "end": actual_end})

        parsed_results = []
        for rec in records:
            m_node = rec["m"]
            props = m_node if isinstance(m_node, dict) else dict(m_node)

            filtered_props = {k: v for k, v in props.items() if
                              (attribute_keys is None or k in attribute_keys) and k not in ["macro_id", "macro_name",
                                                                                            "type"]}

            parsed_results.append({
                "macro_id": props.get("macro_id"),
                "macro_name": props.get("macro_name"),
                "start_segment_id": rec["start_seg"],
                "end_segment_id": rec["end_seg"],
                "properties": filtered_props
            })

        final_results = _apply_python_slice(parsed_results, result_slice)

        return {
            "level": "Macro",
            "type": type,
            "start_macro_id": actual_start,
            "end_macro_id": actual_end,
            "results": final_results
        }

    else:
        # 向上溯源寻找所属 Macro
        seg_cypher = """
                MATCH (s:Segment {type: $type})
                WHERE s.segment_id >= $start AND s.segment_id <= $end
                OPTIONAL MATCH (m:Macro)-[:CONTAINS]->(s)
                ORDER BY s.segment_id ASC
                RETURN s, m.macro_id AS mac_id
                """
        records = client.execute_read(seg_cypher, {"type": type, "start": actual_start, "end": actual_end})

        parsed_results = []
        for rec in records:
            s_node = rec["s"]
            props = s_node if isinstance(s_node, dict) else dict(s_node)

            # 强制保留引擎必需的 segment_summary
            filtered_props = {k: v for k, v in props.items() if
                              (attribute_keys is None or k in attribute_keys or k == "segment_summary") and k not in [
                                  "segment_id", "segment_name", "type"]}

            parsed_results.append({
                "segment_id": props.get("segment_id"),
                "segment_name": props.get("segment_name"),
                "belong_to_macro_id": rec["mac_id"],
                "properties": filtered_props
            })

        final_results = _apply_python_slice(parsed_results, result_slice)

        # 获取该区间内的全局活跃实体
        active_ents_cypher = """
                OPTIONAL MATCH (minor)-[:APPEARED_IN]->(s:Segment {type: $type})
                WHERE s.segment_id >= $start AND s.segment_id <= $end
                WITH collect(DISTINCT minor.name) AS minor_names

                OPTIONAL MATCH (main)-[:HAS_STATE]->(st:State)-[:START_AT]->(cs:Segment {type: $type})
                WHERE cs.segment_id <= $end
                OPTIONAL MATCH (st)-[:END_AT]->(ce:Segment)
                WITH minor_names, main, ce
                WHERE (ce IS NULL OR ce.segment_id >= $start)
                WITH minor_names, collect(DISTINCT {name: main.name, label: labels(main)[0]}) AS main_ents

                RETURN minor_names, main_ents
                """
        ent_records = client.execute_read(active_ents_cypher, {"type": type, "start": actual_start, "end": actual_end})

        ents_summary = {
            "main_entities_by_type": {},
            "minor_entities": []
        }

        if ent_records:
            ents_summary["minor_entities"] = [name for name in ent_records[0]["minor_names"] if name]

            for main_ent in ent_records[0]["main_ents"]:
                if not main_ent["name"]: continue
                label = main_ent["label"]
                if label not in ents_summary["main_entities_by_type"]:
                    ents_summary["main_entities_by_type"][label] = []
                ents_summary["main_entities_by_type"][label].append(main_ent["name"])

        return {
            "level": "Segment",
            "type": type,
            "start_segment_id": actual_start,
            "end_segment_id": actual_end,
            "results": final_results,
            "active_entities_summary": ents_summary
        }


# ==========================================
# 工具2： 主要实体全息状态演进探针
# ==========================================
def _get_entity_state_input_schema():
    _entity_classes_config = app_agents_config.entity_classes_config
    _entity_types = list(_entity_classes_config.keys())

    fields: Dict[str, Any] = {
        "entity_name": (str, Field(..., description="主要实体标准名称（必须是主要实体白名单之一）")),
        "start_segment_id": (Optional[int],
                             Field(default=1, description="起始 Segment ID。负数代表倒数。若不传默认从开头开始。")),
        "end_segment_id": (Optional[int],
                           Field(default=-1, description="结束 Segment ID。负数代表倒数。若不传默认到最后。")),
        "result_slice": (Optional[str],
                         Field(default=None, description="Python 风格的切片语法，用以筛出查询结果全集中的部分结果。"))
    }

    if _entity_types:

        EntityTypeEnum: Any = Enum("EntityTypeEnum", {k: k for k in _entity_types})
        fields["entity_type"] = (EntityTypeEnum | None,
                                 Field(default=None, description="实体类型。如果不确定，输出 null。"))
    else:
        fields["entity_type"] = (Optional[str], Field(default=None, description="实体类型。如果不确定，输出 null。"))

    for ent_type, ent_cfg in _entity_classes_config.items():
        state_props = list(ent_cfg.get("state_properties", {}).keys())
        field_name = f"{ent_type}_attribute_keys"

        if state_props:
            PropEnum = Enum(f"{ent_type}PropEnum", {k: k for k in state_props})
            prop_list_type: Any = GenericAlias(list, (PropEnum,))
            fields[field_name] = (prop_list_type | None, Field(default=None,
                                                               description=f"仅当 entity_type='{ent_type}' 时使用，指定返回更多的状态属性。可选值: {state_props}。若不传则仅返回基础信息。"))
        else:
            fields[field_name] = (Optional[List[str]], Field(default=None,
                                                             description=f"仅当 entity_type='{ent_type}' 时使用，指定返回更多的状态属性。"))

    return create_model("EntityStateInput", **fields)


EntityStateInput = _get_entity_state_input_schema()


@tool("query_entity_state_evolution", args_schema=EntityStateInput)
def query_entity_state_evolution(
        entity_name: str,
        start_segment_id: Optional[int] = 1,
        end_segment_id: Optional[int] = -1,
        entity_type: Optional[str] = None,
        result_slice: Optional[str] = None,
        **kwargs  # 接管所有动态生成的参数（如 Company_attribute_keys）
) -> Dict[str, Any]:
    """
    主要实体状态演进探针。从图数据库中提取主要实体内部属性快照与演进链，自动回传每个状态生效的脉络(Segment)始末。
    """
    client = get_neo4j_client()
    actual_start, actual_end = _resolve_bounds(client, "Segment", start_segment_id, end_segment_id)

    # 根据实际类型，从 kwargs 中抽出白名单
    attribute_keys = None
    if entity_type:
        attribute_keys = kwargs.get(f"{entity_type}_attribute_keys")

    type_filter = f"AND '{entity_type}' IN labels(c)" if entity_type else ""

    state_cypher = f"""
        MATCH (c {{name: $entity_name}})
        WHERE 1=1 {type_filter}
        MATCH (c)-[:HAS_STATE]->(s:State)-[:START_AT]->(cs:Segment)
        OPTIONAL MATCH (s)-[:END_AT]->(ce:Segment)
        WITH c, s, cs, ce
        WHERE cs.segment_id <= $actual_end AND (ce IS NULL OR ce.segment_id >= $actual_start)
        RETURN c.name AS e_name, labels(c)[0] AS e_type, s, cs.segment_id AS start_id, ce.segment_id AS end_id
        ORDER BY start_id ASC
        """

    records = client.execute_read(state_cypher, {
        "entity_name": entity_name,
        "actual_start": actual_start,
        "actual_end": actual_end
    })

    if not records:
        return {
            "entity_name": entity_name,
            "entity_type": entity_type or "Unknown",
            "states_count": 0,
            "states": []
        }

    parsed_states = []
    for rec in records:
        s_node = rec["s"]
        props = s_node if isinstance(s_node, dict) else dict(s_node)

        clean_props = {}
        if attribute_keys is not None:
            for k, v in props.items():
                if k in attribute_keys:
                    # 只要是个字符串，且两头是花括号，就尝试作为字典解包给 LLM
                    if isinstance(v, str) and v.strip().startswith("{") and v.strip().endswith("}"):
                        try:
                            clean_props[k] = json.loads(v)
                        except json.JSONDecodeError:
                            clean_props[k] = v
                    else:
                        clean_props[k] = v

        state_obj = {
            "start_segment_id": rec["start_id"],
            "end_segment_id": rec["end_id"],
            "properties": clean_props
        }

        # 物理相邻去重逻辑
        if parsed_states and parsed_states[-1]["properties"] == state_obj["properties"]:
            parsed_states[-1]["end_segment_id"] = state_obj["end_segment_id"]
        else:
            parsed_states.append(state_obj)

    final_states = _apply_python_slice(parsed_states, result_slice)

    return {
        "entity_name": records[0]["e_name"],
        "entity_type": records[0]["e_type"],
        "states_count": len(final_states),
        "states": final_states
    }


# ==========================================
# 工具3： 主要实体社交与归属关系探针
# ==========================================
def _get_relation_query_input_schema():
    # 提取实体类型白名单
    _entity_classes_config = app_agents_config.entity_classes_config
    _entity_types = list(_entity_classes_config.keys())

    # 提取关系属性全集
    _custom_relations_config = app_agents_config.custom_relations_config
    _all_rel_props = set()
    for rel_cfg in _custom_relations_config.values():
        for prop_key in rel_cfg.get("relation_properties", {}).keys():
            _all_rel_props.add(prop_key)
    _all_rel_props = list(_all_rel_props)

    fields: Dict[str, Any] = {
        "source_entity": (Optional[str], Field(default=None,
                                               description="关系的发起实体名称。关系都是单向的，总是由发起实体指向目标实体。")),
        "target_entity": (Optional[str], Field(default=None, description="关系的目标实体名称。")),
        "start_segment_id": (Optional[int],
                             Field(default=1, description="起始 Segment ID。负数代表倒数。若不传默认从开头开始。")),
        "end_segment_id": (Optional[int],
                           Field(default=-1, description="结束 Segment ID。负数代表倒数。若不传默认到最后。")),
        "result_slice": (Optional[str],
                         Field(default=None, description="Python 风格的切片语法，用以筛出查询结果全集中的部分结果。"))
    }

    if _entity_types:
        EntityTypeEnum: Any = Enum("EntityTypeEnum", {k: k for k in _entity_types})
        fields["source_entity_type"] = (EntityTypeEnum | None, Field(default=None, description="发起实体类型。"))
        fields["target_entity_type"] = (EntityTypeEnum | None, Field(default=None, description="目标实体类型。"))
    else:
        fields["source_entity_type"] = (Optional[str], Field(default=None, description="发起实体类型。"))
        fields["target_entity_type"] = (Optional[str], Field(default=None, description="目标实体类型。"))

    if _all_rel_props:
        RelPropEnum = Enum("RelPropEnum", {k: k for k in _all_rel_props})
        prop_list_type: Any = GenericAlias(list, (RelPropEnum,))
        fields["attribute_keys"] = (prop_list_type | None, Field(default=None,
                                                                 description=f"指定返回更多的关系属性字段。可选值: {_all_rel_props}。若不传则仅返回基础信息。"))
    else:
        fields["attribute_keys"] = (Optional[List[str]], Field(default=None,
                                                               description="指定返回更多的关系属性字段。若不传则仅返回基础信息。"))

    return create_model("RelationQueryInput", **fields)


RelationQueryInput = _get_relation_query_input_schema()


@tool("query_entity_relation_evolution", args_schema=RelationQueryInput)
def query_entity_relation_evolution(
        source_entity: Optional[str] = None,
        source_entity_type: Optional[str] = None,
        target_entity: Optional[str] = None,
        target_entity_type: Optional[str] = None,
        start_segment_id: Optional[int] = 1,
        end_segment_id: Optional[int] = -1,
        result_slice: Optional[str] = None,
        attribute_keys: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    主要实体间关系演进探针。从图数据库中查询主要实体的拓扑连线与演进，回传关系生效的脉络(Segment)区间。
    支持单向扩散与双向验证。注意：必须至少提供 source_entity 或 target_entity 中的一个。
    """
    # 替代 Pydantic 的 @model_validator
    if not source_entity and not target_entity:
        return {"error": "参数校验失败：source_entity 与 target_entity 至少需要提供一个。"}

    client = get_neo4j_client()
    actual_start, actual_end = _resolve_bounds(client, "Segment", start_segment_id, end_segment_id)

    # 直接用传入的 $tgt_type 在运行时校验
    cypher = f"""
        MATCH (src)
        WHERE ($src_name IS NULL OR src.name = $src_name)
          AND ($src_type IS NULL OR $src_type IN labels(src))

        MATCH (tgt)
        WHERE ($tgt_name IS NULL OR tgt.name = $tgt_name)
          AND ($tgt_type IS NULL OR $tgt_type IN labels(tgt))

        MATCH (src)-[:HAS_STATE]->(s:State)-[r:RELATION]->(tgt)
        MATCH (s)-[:START_AT]->(cs:Segment)
        OPTIONAL MATCH (s)-[:END_AT]->(ce:Segment)

        WITH src, tgt, s, r, cs, ce
        WHERE cs.segment_id <= $actual_end 
          AND (ce IS NULL OR ce.segment_id >= $actual_start)

        RETURN src.name AS s_name, labels(src)[0] AS s_type, 
               tgt.name AS t_name, labels(tgt)[0] AS t_type, 
               properties(r) AS rel_props, 
               cs.segment_id AS start_id, ce.segment_id AS end_id
        ORDER BY start_id ASC
        """

    records = client.execute_read(cypher, {
        "src_name": source_entity,
        "src_type": source_entity_type,
        "tgt_name": target_entity,
        "tgt_type": target_entity_type,
        "actual_start": actual_start,
        "actual_end": actual_end
    })

    if not records:
        return {
            "relations_count": 0,
            "relations": []
        }

    parsed_relations = []
    for rec in records:
        rel_obj = {
            "source_entity": rec["s_name"],
            "source_type": rec["s_type"],
            "target_entity": rec["t_name"],
            "target_type": rec["t_type"],
            "start_segment_id": rec["start_id"],
            "end_segment_id": rec["end_id"]
        }

        if attribute_keys is not None:
            props = rec["rel_props"] or {}
            for key in attribute_keys:
                if key in props:
                    val = props[key]
                    if isinstance(val, str) and val.strip().startswith("{") and val.strip().endswith("}"):
                        try:
                            rel_obj[key] = json.loads(val)
                        except json.JSONDecodeError:
                            rel_obj[key] = val
                    else:
                        rel_obj[key] = val

        def _get_rel_props(r):
            return {k: v for k, v in r.items() if k not in ["start_segment_id", "end_segment_id"]}

        if parsed_relations and _get_rel_props(parsed_relations[-1]) == _get_rel_props(rel_obj):
            parsed_relations[-1]["end_segment_id"] = rel_obj["end_segment_id"]
        else:
            parsed_relations.append(rel_obj)

    final_relations = _apply_python_slice(parsed_relations, result_slice)

    return {
        "relations_count": len(final_relations),
        "relations": final_relations
    }


# ==========================================
# 工具4： 次要实体出场轨迹专用探针
# ==========================================
class MinorAppearanceInput(BaseModel):
    entity_name: str = Field(..., description="次要实体名称（未在状态演进系统中的实体）")
    start_segment_id: Optional[int] = Field(default=1, description="起始 Segment ID。负数代表倒数。若不传默认从开头开始。")
    end_segment_id: Optional[int] = Field(default=-1, description="结束 Segment ID。负数代表倒数。若不传默认到最后。")
    result_slice: Optional[str] = Field(default=None,
                                        description="Python 风格的切片语法，用以筛出查询结果全集中的部分结果。")


@tool("query_minor_entity_appearances", args_schema=MinorAppearanceInput)
def query_minor_entity_appearances(
        entity_name: str,
        start_segment_id: Optional[int] = 1,
        end_segment_id: Optional[int] = -1,
        result_slice: Optional[str] = None
) -> Dict[str, Any]:
    """
    次要实体轨迹提取器。从图数据库中查询次要实体在各脉络(Segment)的轻量级出场事件摘要。
    """
    client = get_neo4j_client()
    actual_start, actual_end = _resolve_bounds(client, "Segment", start_segment_id, end_segment_id)

    # 用 OPTIONAL MATCH 确保即使在区间内没出场，也能查出 entity_type

    cypher = """
    MATCH (e {name: $entity_name})
    OPTIONAL MATCH (e)-[app:APPEARED_IN]->(s:Segment)
    WHERE s.segment_id >= $actual_start AND s.segment_id <= $actual_end
    RETURN labels(e)[0] AS entity_type, 
           s.segment_id AS segment_id, 
           s.segment_name AS segment_name, 
           app.summary AS summary
    ORDER BY segment_id ASC
    """

    records = client.execute_read(cypher, {
        "entity_name": entity_name,
        "actual_start": actual_start,
        "actual_end": actual_end
    })

    if not records:
        return {
            "entity_name": entity_name,
            "entity_type": "Unknown",
            "total_appearances": 0,
            "appearances": []
        }

    resolved_type = records[0]["entity_type"]

    parsed_appearances = []
    for rec in records:
        if rec["segment_id"] is not None:
            parsed_appearances.append({
                "segment_id": rec["segment_id"],
                "segment_name": rec["segment_name"],
                "appearance_summary": rec["summary"] or ""
            })

    final_appearances = _apply_python_slice(parsed_appearances, result_slice)

    return {
        "entity_name": entity_name,
        "entity_type": resolved_type,
        "total_appearances": len(final_appearances),
        "appearances": final_appearances
    }


# ==========================================
# 工具5： 微观原文探针 (Qdrant 向量化隔离)
# ==========================================
def _get_qdrant_probe_input_schema():
    _custom_payload_fields = list(app_agents_config.custom_payload_fields.keys())

    _all_attr_keys = ["entities", "chunk_text"] + _custom_payload_fields

    fields: Dict[str, Any] = {
        "query_text": (str,
                       Field(..., description="具体的细节描述、关键台词、动作行为、时空、意图等，作为Qdrant的查询query")),
        "start_segment_id": (Optional[int],
                             Field(default=1, description="起始 Segment ID。负数代表倒数。若不传默认从开头开始。")),
        "end_segment_id": (Optional[int],
                           Field(default=-1, description="结束 Segment ID。负数代表倒数。若不传默认到最后。")),
        "type": (Optional[str], Field(default="原文", description="所属分支，默认为'原文'")),
        "entities": (Optional[List[str]], Field(default=None, description="出场实体白名单过滤，仅支持精确搜索")),
        "top_k": (Optional[int], Field(default=3, description="返回最匹配的切片数量，一般不超过5个"))
    }

    if _all_attr_keys:
        AttrEnum = Enum("QdrantAttrEnum", {k: k for k in _all_attr_keys})
        attr_list_type: Any = GenericAlias(list, (AttrEnum,))
        fields["attribute_keys"] = (attr_list_type | None, Field(default=None,
                                                                 description=f"指定返回更多的切片属性字段。可选值: {_all_attr_keys}。若不传则仅返回基础信息：point_id, score, type, segment_id, segment_name, chunk_index。"))
    else:
        fields["attribute_keys"] = (Optional[List[str]], Field(default=None,
                                                               description="指定返回更多的切片属性字段。若不传则仅返回基础信息。"))

    return create_model("QdrantProbeInput", **fields)


QdrantProbeInput = _get_qdrant_probe_input_schema()


@tool("probe_qdrant_semantic_chunks", args_schema=QdrantProbeInput)
def probe_qdrant_semantic_chunks(
        query_text: str,
        start_segment_id: Optional[int] = 1,
        end_segment_id: Optional[int] = -1,
        type: Optional[str] = "原文",
        entities: Optional[List[str]] = None,
        top_k: Optional[int] = 3,
        attribute_keys: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    微观原文切片探针。在向量数据库中执行双向量混合检索，提取切片元数据及原文描写。
    可以承接图数据库工具查出的脉络范围、出场实体等作为过滤条件。
    """

    if top_k is None or top_k > 5:
        top_k = 5
    if top_k < 1:
        top_k = 1

    neo4j_client = get_neo4j_client()
    qdrant_service = get_qdrant_service()
    type = "原文" if type is None else type

    try:
        # 从 Neo4j 获取 max_id，解析 Qdrant 的过滤边界
        actual_start, actual_end = _resolve_bounds(neo4j_client, "Segment", start_segment_id, end_segment_id)
        segment_range = {"gte": actual_start, "lte": actual_end}

        # 调用封装好的 QdrantService 执行 Embedding 和双路混合搜索

        raw_chunks = qdrant_service.search_relevant_chunks(
            query_text=query_text,
            segment_range=segment_range,
            entities=entities,
            type=type,
            top_k=top_k
        )

        # 字段白名单过滤与组装
        parsed_chunks = []
        for chunk in raw_chunks:
            chunk_obj = {
                "point_id": chunk["point_id"],
                "score": chunk["score"],
                "type": chunk["type"],
                "segment_id": chunk["segment_id"],
                "segment_name": chunk["segment_name"],
                "chunk_index": chunk["chunk_index"],
            }
            if attribute_keys is not None:
                for k in attribute_keys:

                    if k == "chunk_text":
                        chunk_obj["chunk_text"] = chunk.get("text")
                    else:
                        chunk_obj[k] = chunk.get(k)
            parsed_chunks.append(chunk_obj)

        return {"chunks": parsed_chunks}

    except Exception as e:
        sys_logger.error(f"Qdrant 微观原文探针执行失败: {e}")
        return {"chunks": []}
