import json
from collections import defaultdict
from app.database.neo4j_client import Neo4jClient
from app.utils.logger import sys_logger


class Reader02GraphService:
    def __init__(self):
        self.client = Neo4jClient()

    def close(self):
        self.client.close()


    def get_previous_segment_summary(self, current_segment_num: int) -> str:
        """
        从 Neo4j 获取上一段（Segment）的摘要
        """
        if current_segment_num <= 1:
            return "这是第一段，无前情提要。"

        cypher = """
                MATCH (s:Segment {segment_id: $prev_id})
                RETURN s.segment_summary AS summary
                """
        records = self.client.execute_read(cypher, {"prev_id": current_segment_num - 1})
        if records and records[0].get("summary"):
            return records[0]["summary"]

        return "无前情摘要。"


    def get_entities_latest_states(self, entities: list) -> dict:
        """
        根据名字和拓扑特征提取叶子 State 节点
        """
        if not entities:
            return {}

        cypher = """
                UNWIND $entities AS ent_name
                MATCH (c {name: ent_name})
                OPTIONAL MATCH (c)-[:HAS_STATE]->(s:State) WHERE NOT (s)-[:NEXT]->()
                RETURN c.name AS entity_name, labels(c)[0] AS entity_type, properties(s) AS state_props
                """
        records = self.client.execute_read(cypher, {"entities": entities})

        result = {}
        for rec in records:
            ent_name = rec["entity_name"]
            ent_type = rec["entity_type"]
            props = rec["state_props"]

            if props is None:
                result[ent_name] = {"entity_type": ent_type}
            else:
                clean_props = {"entity_type": ent_type}
                for k, v in props.items():
                    # 若属性值为 JSON 对象字符串，则自动还原为 dict 供 LLM 阅读
                    if isinstance(v, str) and v.strip().startswith("{") and v.strip().endswith("}"):
                        try:
                            clean_props[k] = json.loads(v)
                        except json.JSONDecodeError:
                            clean_props[k] = v
                    else:
                        clean_props[k] = v
                result[ent_name] = clean_props

        sys_logger.info(f"已获得实体{entities}的当前最新状态")
        # sys_logger.debug(f"最新状态为{result}")

        return result


    def get_entities_latest_relations(self, entities: list) -> list:
        """
        通过最新状态节点找出所有对外的 RELATION 连线
        """
        if not entities:
            return []

        cypher = """
                UNWIND $entities AS ent_name
                MATCH (c {name: ent_name})
                OPTIONAL MATCH (c)-[:HAS_STATE]->(s:State) WHERE NOT (s)-[:NEXT]->()
                OPTIONAL MATCH (s)-[r:RELATION]->(target)
                WHERE r IS NOT NULL AND target.name IN $entities
                RETURN c.name AS source_entity, labels(c)[0] AS source_type,
                       target.name AS target_entity, labels(target)[0] AS target_type,
                       properties(r) AS rel_props
                """
        records = self.client.execute_read(cypher, {"entities": entities})

        relations = []
        for rec in records:
            if rec["target_entity"]:
                relations.append({
                    "source_entity": rec["source_entity"],
                    "source_type": rec["source_type"],
                    "target_entity": rec["target_entity"],
                    "target_type": rec["target_type"],
                    "relation_properties": rec["rel_props"]
                })

        sys_logger.info(f"已获得实体{entities}的当前最新关系")
        # sys_logger.debug(f"最新关系为{relations}")

        return relations


    def execute_segment_updates(self, segment_num: int, state_delta: dict, relation_delta: dict):

        # 归一化提取实体（整合状态与关系发起者）
        active_entities_map = {}

        for label, entity_list in state_delta.items():
            if label == "segment_num" or not isinstance(entity_list, list):
                continue

            for item in entity_list:
                if not isinstance(item, dict):
                    continue
                ent_name = item.get("entity_name")
                if not ent_name:
                    continue

                props = {}
                for k, v in item.items():
                    if k == "entity_name":
                        continue
                    # 过滤未发生变化的空值 (None, "", [], {})
                    if v in [None, "", [], {}]:
                        continue
                    if isinstance(v, dict):
                        if not any(val for val in v.values() if val not in [None, ""]):
                            continue
                        props[k] = json.dumps(v, ensure_ascii=False)
                    else:
                        props[k] = v

                # 只有当存在有效增量属性时，才触发 State 节点更新
                if props:
                    active_entities_map[ent_name] = {"label": label, "props": props}

        # 实体发起了连线但没变更状态，需强行创建一个空字典状态以便后续挂载连线
        # 如果关系变更的实体已经在 active_entities_map 里了，不用管
        for rel_group_key, rel_list in relation_delta.items():
            if rel_group_key == "segment_num" or not isinstance(rel_list, list):
                continue

            for rel in rel_list:
                if not isinstance(rel, dict):
                    continue
                src = rel.get("source_entity")
                src_type = rel.get("source_type")
                # 检查该条关系是否包含实质性的非空属性更新
                has_valid_rel_props = any(
                    v not in [None, "", [], {}]
                    for k, v in rel.items()
                    if k not in {"source_entity", "source_type", "target_entity", "target_type"}
                )
                if src and src_type and has_valid_rel_props and src not in active_entities_map:
                    active_entities_map[src] = {"label": src_type, "props": {}}

        sys_logger.info(f"已获得需要进行操作（状态/关系变化）的实体清单")
        sys_logger.debug(f"active_entities_map: {active_entities_map}")

        if not active_entities_map:
            sys_logger.info(f"第 {segment_num} 段无状态或关系变动，跳过 Neo4j 写入。")
            return

        # 按 Label 归类，确保 `MATCH (c:%s)` 精准锁定同名但不跨域的实体
        label_groups = defaultdict(list)
        for ent_name, data in active_entities_map.items():
            if data["label"]:
                label_groups[data["label"]].append({"name": ent_name, "props": data["props"]})

        spawn_cypher_template = """
                UNWIND $updates AS update
                MATCH (c:%s {name: update.name})
                OPTIONAL MATCH (c)-[:HAS_STATE]->(old_s:State) WHERE NOT (old_s)-[:NEXT]->()

                // 1. 创建新状态节点，继承旧状态全量属性并覆写本段增量属性
                CREATE (new_s:State)
                SET new_s = coalesce(properties(old_s), {})
                SET new_s += update.props
                MERGE (c)-[:HAS_STATE]->(new_s)

                // 2. 锚定当前 Segment 时间轴，并将旧状态封口
                WITH c, old_s, new_s
                MATCH (seg:Segment {segment_id: $segment_num})
                MERGE (new_s)-[:START_AT]->(seg)
                FOREACH (_ IN CASE WHEN old_s IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (old_s)-[:NEXT]->(new_s)
                    MERGE (old_s)-[:END_AT]->(seg)
                )

                // 3. 将旧状态上的所有 RELATION 边无损克隆继承至新状态节点
                WITH old_s, new_s
                OPTIONAL MATCH (old_s)-[r_old:RELATION]->(t)
                FOREACH (_ IN CASE WHEN r_old IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (new_s)-[r_new:RELATION]->(t)
                    SET r_new = properties(r_old)
                )
                """
        for label, updates in label_groups.items():
            self.client.execute_write(
                spawn_cypher_template % label,
                {"updates": updates, "segment_num": segment_num}
            )

        sys_logger.info("已执行主要实体状态更新，准备关系更新")

        relation_groups = defaultdict(list)
        anchor_keys = {"source_entity", "source_type", "target_entity", "target_type"}

        for rel_group_key, rel_list in relation_delta.items():
            if rel_group_key == "segment_num" or not isinstance(rel_list, list):
                continue

            for rel in rel_list:
                if not isinstance(rel, dict):
                    continue
                src_name = rel.get("source_entity")
                src_lbl = rel.get("source_type")
                tgt_name = rel.get("target_entity")
                tgt_lbl = rel.get("target_type")

                if not (src_name and src_lbl and tgt_name and tgt_lbl):
                    continue

                props = {
                    k: (json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v)
                    for k, v in rel.items()
                    if k not in anchor_keys and v not in [None, "", [], {}]
                }
                if props:
                    relation_groups[(src_lbl, tgt_lbl)].append({
                        "src": src_name,
                        "tgt": tgt_name,
                        "props": props
                    })

        sys_logger.info(f"已获得需要更新的主要实体关系")
        sys_logger.debug(f"relation_groups: {relation_groups}")

        upsert_cypher_template = """
                UNWIND $rels AS rel
                MATCH (src:%s {name: rel.src})-[:HAS_STATE]->(s:State) WHERE NOT (s)-[:NEXT]->()
                MATCH (tgt:%s {name: rel.tgt})
                MERGE (s)-[r:RELATION]->(tgt)
                SET r += rel.props
                """

        for (src_lbl, tgt_lbl), rels in relation_groups.items():
            if not src_lbl or not tgt_lbl:
                continue
            cypher = upsert_cypher_template % (src_lbl, tgt_lbl)
            self.client.execute_write(cypher, {"rels": rels})

        sys_logger.info("已执行主要实体关系更新")
