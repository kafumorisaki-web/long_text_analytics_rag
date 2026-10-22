import json
import os
import sys

from app.database.neo4j_client import Neo4jClient
import root_running_config
from app.utils.logger import sys_logger
from collections import defaultdict
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())


def init_neo4j_skeleton(neo4j_client: Neo4jClient, segments: list):
    """
    铺设 Macro 和 Segment 的空壳与 NEXT 连线
    """
    sys_logger.info("正在向 Neo4j 注入宏观骨架")

    macro_instances = getattr(root_running_config, "_schema", {}).get("macro_nodes", {}).get("instances", [])
    if not macro_instances:
        macro_instances = [{
            "macro_id": 1,
            "macro_name": "默认归纳节点",
            "segment_range": [segments[0]["num"], segments[-1]["num"]]
        }]

    # Macro 与 Segment
    cypher_merge_nodes = """
    MERGE (m:Macro {macro_id: $macro_id})
    ON CREATE SET m.macro_name = $macro_name, m.type = "原文"
    WITH m
    UNWIND $segments_list AS item
    MERGE (s:Segment {segment_id: item.num})
    ON CREATE SET s.segment_name = item.title, s.type = "原文"
    MERGE (m)-[:CONTAINS]->(s)
    RETURN count(s) as total
    """
    for inst in macro_instances:
        start_id, end_id = inst.get("segment_range", [1, -1])
        target_segments = [seg for seg in segments if start_id <= seg["num"] <= end_id]
        if target_segments:
            neo4j_client.execute_write(cypher_merge_nodes, {
                "macro_id": inst["macro_id"],
                "macro_name": inst["macro_name"],
                "segments_list": [{"num": s["num"], "title": s["title"]} for s in target_segments]
            })

    # Segment 之间的 NEXT 时间线
    cypher_segment_next = """
    UNWIND $next_pairs AS pair
    MATCH (s1:Segment {segment_id: pair.prev})
    MATCH (s2:Segment {segment_id: pair.curr})
    MERGE (s1)-[:NEXT]->(s2)
    """
    next_pairs = [{"prev": segments[i]["num"], "curr": segments[i + 1]["num"]} for i in range(len(segments) - 1)]
    if next_pairs:
        neo4j_client.execute_write(cypher_segment_next, {"next_pairs": next_pairs})

    #  Macro 之间的 NEXT 时间线
    if len(macro_instances) > 1:
        cypher_macro_next = """
        UNWIND $next_pairs AS pair
        MATCH (m1:Macro {macro_id: pair.prev})
        MATCH (m2:Macro {macro_id: pair.curr})
        MERGE (m1)-[:NEXT]->(m2)
        """
        sorted_macros = sorted(macro_instances, key=lambda x: x["macro_id"])
        macro_next_pairs = [{"prev": sorted_macros[i]["macro_id"], "curr": sorted_macros[i + 1]["macro_id"]} for i in
                            range(len(sorted_macros) - 1)]
        neo4j_client.execute_write(cypher_macro_next, {"next_pairs": macro_next_pairs})


def ingest_macro_properties(neo4j_client: Neo4jClient, macro_jsonl_path: str):
    """
    为 Macro节点挂载归纳属性
    """
    if not os.path.exists(macro_jsonl_path):
        return

    sys_logger.info("正在为 Macro 节点挂载归纳属性")
    cypher = """
    MATCH (m:Macro {macro_id: $macro_id})
    SET m += $props
    """
    success_count = 0
    with open(macro_jsonl_path, 'r', encoding='utf-8') as f:
        for line in f:
            if not line.strip(): continue
            try:
                data = json.loads(line)
                m_id = data.pop("macro_id")
                neo4j_client.execute_write(cypher, {"macro_id": m_id, "props": data})
                success_count += 1
            except Exception as e:
                sys_logger.error(f"Macro 属性写入异常: {e}")

    sys_logger.info(f"成功挂载 {success_count} 条 Macro 属性。")


def ingest_segment_properties(neo4j_client: Neo4jClient, jsonl_path: str):
    """
    挂载脉络节点的所有属性（摘要 + 用户自定义属性）
    """
    sys_logger.info("正在为 Segment 节点挂载属性与事实摘要")

    # 利用 += 动态合并字典
    cypher_template = """
    MATCH (s:Segment {segment_id: $segment_id, segment_name: $segment_name})
    SET s.segment_summary = $segment_summary
    SET s += $custom_props
    RETURN s.segment_id AS updated_id
    """

    success_count, fail_count = 0, 0
    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            if not line.strip(): continue
            try:
                data = json.loads(line)
            except Exception:
                continue

            segment_num = data.get("segment_num")
            segment_title = data.get("segment_title")
            summary = data.get("segment_summary")

            if segment_num is None or not segment_title or not summary:
                fail_count += 1
                continue

            # 剥离系统预留字段，剩下的全部视为 custom_properties 直接动态塞给 Neo4j
            reserved_keys = {"segment_num", "segment_title", "segment_summary", "extracted_entities"}
            custom_props = {k: v for k, v in data.items() if k not in reserved_keys}

            params = {
                "segment_id": segment_num,
                "segment_name": segment_title,
                "segment_summary": summary,
                "custom_props": custom_props
            }

            try:
                records = neo4j_client.execute_write(cypher_template, params)
                if records:
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                sys_logger.error(f"写入属性异常 [ID: {segment_num}]: {e}")
                fail_count += 1

    sys_logger.info(f"入库结算：成功 {success_count} 段，失败 {fail_count} 段。")
    if fail_count > 0:
        raise RuntimeError(f"有 {fail_count} 个脉络节点属性写入失败！图谱已被污染，请查阅日志。")


def ingest_entities(neo4j_client: Neo4jClient, jsonl_path: str, primary_list_path: str, minor_list_path: str):

    sys_logger.info("开始在内存中构建实体时空轨迹网，并写入图谱")

    entity_history = defaultdict(lambda: {"type": "Entity", "appearances": []})

    # 扫描账本提取轨迹
    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line in f:
            if not line.strip(): continue
            try:
                data = json.loads(line)
            except:
                continue

            seg_num = data.get("segment_num")
            if not seg_num: continue

            for ent in data.get("extracted_entities", []):
                raw_name = ent.get("raw_name")
                # 如果没有类型，默认给个 Entity
                ent_type = ent.get("type", "Entity")
                local_summary = ent.get("local_summary", "")

                if raw_name:
                    entity_history[raw_name]["type"] = ent_type
                    entity_history[raw_name]["appearances"].append({
                        "segment_num": seg_num,
                        "summary": local_summary
                    })

    for name, record in entity_history.items():
        record["appearances"].sort(key=lambda x: x["segment_num"])

    def _process_entity_list(entity_path: str, is_minor: bool):
        if not os.path.exists(entity_path): return

        with open(entity_path, 'r', encoding='utf-8') as f:
            entity_dict = json.load(f)

        success_count = 0
        # 遍历：外层是类型，内层是名字
        for dict_ent_type, entity_names in entity_dict.items():

            label = dict_ent_type.strip() or "Entity"

            for name in entity_names:
                history = entity_history.get(name)
                if not history or not history["appearances"]: continue

                debut_segment = history["appearances"][0]["segment_num"]

                # 创建蓝节点 + DEBUTED_IN 连线
                cypher_debut = f"""
                MERGE (n:{label} {{name: $name}})
                SET n.type = '原文'
                WITH n
                MATCH (s:Segment {{segment_id: $debut_segment}})
                MERGE (n)-[:DEBUTED_IN]->(s)
                RETURN elementId(n) AS node_id
                """
                neo4j_client.execute_write(cypher_debut, {"name": name, "debut_segment": debut_segment})

                # 次要实体 (APPEARED_IN -> Segment)
                if is_minor:
                    cypher_appearances = f"""
                    UNWIND $appearances AS app
                    MATCH (n:{label} {{name: $name}})
                    MATCH (s:Segment {{segment_id: app.segment_num}})
                    MERGE (n)-[r:APPEARED_IN]->(s)
                    SET r.summary = app.summary
                    """
                    neo4j_client.execute_write(cypher_appearances, {
                        "name": name,
                        "appearances": history["appearances"]
                    })
                success_count += 1

        tag = "次要" if is_minor else "主要"
        sys_logger.info(f"✅ {tag}实体入库结算：成功锚定 {success_count} 个蓝节点。")

    _process_entity_list(primary_list_path, is_minor=False)
    _process_entity_list(minor_list_path, is_minor=True)


def main():
    sys_logger.info("启动 Reader01 图谱写入...")

    clean_segment_jsonl_path = root_running_config.run_pass1_pipeline_clean_ledger
    primary_list_path = root_running_config.final_approved_main_path
    minor_list_path = root_running_config.final_approved_minor_path
    temp_segments_path = root_running_config.run_pass1_pipeline_temp_segments_path
    macro_jsonl_path = root_running_config.run_pass1_pipeline_macro_ledger_path

    try:
        neo4j_client = Neo4jClient()
    except Exception as e:
        sys_logger.error(f"引擎启动失败，进程终止: {e}")
        sys.exit(1)

    try:
        # 读取本地的基础切片
        with open(temp_segments_path, 'r', encoding='utf-8') as f:
            segments = json.load(f)

        # 执行图谱落盘三大阶段
        init_neo4j_skeleton(neo4j_client, segments)
        ingest_macro_properties(neo4j_client, macro_jsonl_path)
        ingest_segment_properties(neo4j_client, clean_segment_jsonl_path)
        ingest_entities(neo4j_client, clean_segment_jsonl_path, primary_list_path, minor_list_path)

        sys_logger.info("Reader01 数据落盘闭环完成！")
    finally:
        neo4j_client.close()


if __name__ == "__main__":
    main()
