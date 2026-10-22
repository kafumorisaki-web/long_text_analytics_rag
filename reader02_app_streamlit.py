import time

import streamlit as st
import json
import importlib
import root_running_config
import app.graph.app_graph_config
from app.database.neo4j_client import Neo4jClient
from dotenv import load_dotenv, find_dotenv

import ast

load_dotenv(find_dotenv())


# @st.cache_resource（防断连引擎）：专门用来缓存全局资源对象（比如 Neo4jClient 的数据库长连接）。
# 如果不加这个，每点一次下拉框，它就会去向 Neo4j 重新发起一次 TCP 握手验证，建立一个新的 Driver。这不仅极度卡顿，还极容易触发数据库的最大连接数限制直接报错。
# 加上它，只要实例建好了，后续不管页面怎么刷新，都是直接复用内存里的同一个网络连接。
@st.cache_resource
def get_neo4j_client():
    return Neo4jClient()


client = get_neo4j_client()


# @st.cache_data(ttl=300)（防爆破检索）：专门用来缓存查询出来的数据结果（比如提取所有实体的清单）。
# 因为提取全图谱实体这个查询要扫很多节点，如果每次渲染都去扫一遍，Neo4j 会承受无意义的 IO 压力。加上这个，它会把第一次查回来的数据直接冻结在内存里，ttl=300 意味着这波数据的保质期是 5 分钟（300 秒）。
# 5 分钟内再怎么查，都直接秒回内存里的死数据，超过 5 分钟才会重新查一次更新。
@st.cache_data(ttl=3000)
def fetch_all_entities():
    """
    提取主要实体清单供下拉框使用。
    底层机制：利用 EXISTS { (n)-[:HAS_STATE]->() } 语法作为拓扑隔离带，
    直接在查询层屏蔽掉只有 [:APPEARED_IN] 连线的次要实体，保证入选的绝对是主要实体。
    """

    cypher = """
        MATCH (n) 
        WHERE EXISTS { (n)-[:HAS_STATE]->() }
        RETURN n.name AS name, labels(n)[0] AS type
        ORDER BY type, name
        """

    records = client.execute_read(cypher)
    return [f"{r['name']} ({r['type']})" for r in records]


# 反序列化
def _safe_eval(val_str: str):
    if val_str.startswith("[") and val_str.endswith("]"):
        try:
            return ast.literal_eval(val_str)
        except Exception:
            return val_str
    return val_str


def get_deduped_state_history(entity_name: str, client):
    """
    拉取实体的所有状态，并在 Python 层进行哈希去重。
    全量属性比对。只要当前属性字典与上一个记录的字典存在任何差异，即视为有效演进。
    """

    cypher = """
        MATCH (e {name: $name})-[:HAS_STATE]->(s:State)-[:START_AT]->(ch:Segment)
        RETURN elementId(s) AS state_id, properties(s) AS props,
               ch.segment_id AS start_ch_id, ch.segment_name AS start_ch_name,
               ch.segment_summary AS ch_summary
        ORDER BY ch.segment_id ASC
        """
    records = client.execute_read(cypher, {"name": entity_name})

    deduped_states = []
    last_props_hash = None

    for r in records:
        # 直接拿全量属性字典进行字典序序列化比对
        props_str = json.dumps(r["props"], sort_keys=True)

        if props_str != last_props_hash:
            deduped_states.append(r)
            last_props_hash = props_str

    return deduped_states


def update_state_in_neo4j(entity_name: str, old_props: dict, new_props: dict, client):
    """查找该实体名下所有与 old_props 完全一致的节点，集体覆盖为 new_props"""
    cypher_find = """
    MATCH (e {name: $name})-[:HAS_STATE]->(s:State)
    RETURN elementId(s) AS state_id, properties(s) AS props
    """
    records = client.execute_read(cypher_find, {"name": entity_name})

    ids_to_update = []

    old_clean = {k: v for k, v in old_props.items() if v}

    for r in records:
        db_clean = {k: v for k, v in r["props"].items() if v}
        if db_clean == old_clean:
            ids_to_update.append(r["state_id"])

    if ids_to_update:
        # SET s = $new_props 会直接用新字典物理替换掉节点上的旧字典，实现全覆盖与字段删除
        cypher_update = """
        UNWIND $state_ids AS sid
        MATCH (s:State) WHERE elementId(s) = sid
        SET s = $new_props
        """
        client.execute_write(cypher_update, {"state_ids": ids_to_update, "new_props": new_props})
    return len(ids_to_update)


def get_deduped_relation_history(source_name: str, target_name: str, client):
    """
    双向拉取 A与B 之间的所有历史关系，按照明确的指向分为 A->B 和 B->A 两条轨道分别去重。
    """

    cypher = """
        CALL {
            MATCH (src {name: $src_name})-[:HAS_STATE]->(s:State)-[r:RELATION]->(tgt {name: $tgt_name})
            MATCH (s)-[:START_AT]->(ch:Segment)
            RETURN 'A_to_B' AS direction, elementId(r) AS rel_id, properties(r) AS props,
                   ch.segment_id AS start_ch_id, ch.segment_name AS start_ch_name,
                   ch.segment_summary AS ch_summary
            UNION ALL
            MATCH (src {name: $tgt_name})-[:HAS_STATE]->(s:State)-[r:RELATION]->(tgt {name: $src_name})
            MATCH (s)-[:START_AT]->(ch:Segment)
            RETURN 'B_to_A' AS direction, elementId(r) AS rel_id, properties(r) AS props,
                   ch.segment_id AS start_ch_id, ch.segment_name AS start_ch_name,
                   ch.segment_summary AS ch_summary
        }
        RETURN direction, rel_id, props, start_ch_id, start_ch_name, ch_summary
        ORDER BY start_ch_id ASC
        """
    records = client.execute_read(cypher, {"src_name": source_name, "tgt_name": target_name})

    deduped_rels = []
    # 维护两条独立轨道的哈希值
    last_hash_A_to_B = None
    last_hash_B_to_A = None

    for r in records:
        # 全量字典序列化
        props_str = json.dumps(r["props"], sort_keys=True)

        if r["direction"] == 'A_to_B':
            if props_str != last_hash_A_to_B:
                deduped_rels.append(r)
                last_hash_A_to_B = props_str
        else:
            if props_str != last_hash_B_to_A:
                deduped_rels.append(r)
                last_hash_B_to_A = props_str

    return deduped_rels


def update_relation_in_neo4j(src_name: str, tgt_name: str, direction: str, old_props: dict, new_props: dict, client):
    """查找该方向上所有与 old_props 完全一致的连线，集体覆盖为 new_props"""
    if direction == "A_to_B":
        cypher_find = """
        MATCH (src {name: $src_name})-[:HAS_STATE]->(s:State)-[r:RELATION]->(tgt {name: $tgt_name})
        RETURN elementId(r) AS rel_id, properties(r) AS props
        """
    else:
        cypher_find = """
        MATCH (src {name: $tgt_name})-[:HAS_STATE]->(s:State)-[r:RELATION]->(tgt {name: $src_name})
        RETURN elementId(r) AS rel_id, properties(r) AS props
        """
    records = client.execute_read(cypher_find, {"src_name": src_name, "tgt_name": tgt_name})

    ids_to_update = []
    old_clean = {k: v for k, v in old_props.items() if v}
    for r in records:
        db_clean = {k: v for k, v in r["props"].items() if v}
        if db_clean == old_clean:
            ids_to_update.append(r["rel_id"])

    if ids_to_update:
        cypher_update = """
        UNWIND $rel_ids AS rid
        MATCH ()-[r:RELATION]->() WHERE elementId(r) = rid
        SET r = $new_props
        """
        client.execute_write(cypher_update, {"rel_ids": ids_to_update, "new_props": new_props})
    return len(ids_to_update)


def render_reader02_page():
    st.title("Reader02: 实体演进视图")

    with st.expander("Step 1: 启动解析引擎", expanded=True):
        st.info(
            "点击启动后，系统将调用 LLM 逐段推演主要实体的状态与关系演进并写入 Neo4j。请确保后台 Terminal 处于可见状态以观测日志。")
        if st.button("一键执行 Reader02 Pipeline", type="primary", use_container_width=True):
            with st.spinner("引擎高负荷运转中，请观察后台控制台日志，切勿刷新页面..."):
                try:
                    importlib.reload(root_running_config)
                    importlib.reload(app.graph.app_graph_config)
                    from reader02_run_evolution import run_reader02
                    run_reader02()
                    fetch_all_entities.clear()
                    st.success("Reader02 演进任务执行完成！")
                except Exception as e:
                    st.error(f"演进任务异常崩溃: {e}")

    all_entities_list = fetch_all_entities()
    page_mode = st.sidebar.radio("请选择审查维度：", ["实体状态演进 (State)", "实体关系演进 (Relation)"])

    if page_mode == "实体状态演进 (State)":
        st.subheader("实体单线状态演进 (标红🔴字段代表本段发生突变)")
        selected_entity_raw = st.selectbox("请选择要审查的实体：", all_entities_list)

        if selected_entity_raw:
            # 修复：使用 rsplit 从右侧切分，防止实体名称自带括号导致解析崩溃
            entity_name, entity_type = selected_entity_raw.rsplit(" (", 1)
            entity_type = entity_type[:-1]  # 剥离出实体类型

            states = get_deduped_state_history(entity_name, client)

            if not states:
                st.info(f"实体 '{entity_name}' 暂无任何 State 流转记录。")
            else:
                st.success(f"共发现 {len(states)} 次有效状态突变。当前实体类型：{entity_type}")

                prev_props = {}
                for idx, state_data in enumerate(states):
                    props = state_data["props"]
                    ch_id = state_data["start_ch_id"]

                    with st.expander(f"第 {ch_id} 段：{state_data['start_ch_name']}", expanded=(idx == len(states) - 1)):
                        st.caption("本段摘要：" + str(state_data["ch_summary"] or ""))

                        with st.form(key=f"form_state_{state_data['state_id']}"):
                            new_props = {}

                            state_cfg = root_running_config._schema.get("entity_system", {}).get("entity_classes",
                                                                                                 {}).get(entity_type,
                                                                                                         {}).get(
                                "state_properties", {})
                            short_fields = [k for k, v in state_cfg.items() if
                                            v.get("max_length") and int(v["max_length"]) <= 20]
                            long_fields = [k for k in state_cfg.keys() if k not in short_fields]
                            nested_fields = []

                            known_fields = set(short_fields + long_fields + nested_fields)

                            def get_label(field, display_name):
                                return f"🔴 {display_name}" if (
                                        idx > 0 and props.get(field, "") != prev_props.get(field, "")) else display_name

                            # 渲染短字段
                            if short_fields:
                                s_cols = st.columns(3)
                                for i, f in enumerate(short_fields):
                                    val = props.get(f, "")
                                    with s_cols[i % 3]:
                                        input_val = st.text_input(get_label(f, f.capitalize()), value=val,
                                                                  key=f"s_{f}_{state_data['state_id']}")
                                        if input_val.strip():
                                            new_props[f] = _safe_eval(input_val.strip())

                            # 渲染长字段
                            if long_fields:
                                l_cols = st.columns(3)
                                for i, f in enumerate(long_fields):
                                    val = props.get(f, "")
                                    with l_cols[i % 3]:
                                        ta_val = st.text_area(get_label(f, f.capitalize()), value=val, height=68,
                                                              key=f"l_{f}_{state_data['state_id']}")
                                        if ta_val.strip():
                                            new_props[f] = _safe_eval(ta_val.strip())

                            # 渲染扩展字段
                            extra_fields = [k for k in props.keys() if k not in known_fields and k != "entity_type"]
                            if extra_fields:
                                st.markdown("---")
                                st.markdown("**扩展属性 (非标准 Schema 字段或跨界属性)**")
                                e_cols = st.columns(3)
                                for i, f in enumerate(extra_fields):
                                    val = props.get(f, "")
                                    with e_cols[i % 3]:
                                        ta_val = st.text_area(get_label(f, f.capitalize()), value=str(val), height=68,
                                                              key=f"e_{f}_{state_data['state_id']}")
                                        if ta_val.strip():
                                            new_props[f] = _safe_eval(ta_val.strip())

                            # 渲染复杂字典属性
                            if nested_fields:
                                st.markdown("---")
                                for f in nested_fields:
                                    val = props.get(f, "")
                                    if isinstance(val, str) and val.startswith("{"):
                                        try:
                                            val = json.loads(val)
                                        except:
                                            pass

                                    label_prefix = "🔴 " if (idx > 0 and props.get(f) != prev_props.get(f)) else ""
                                    st.markdown(f"**{label_prefix}{f.capitalize()}**")

                                    if isinstance(val, dict) or val == "":
                                        inner_cols = st.columns(2)
                                        with inner_cols[0]:
                                            t1 = st.text_area(f"Summary",
                                                              value=val.get("summary", "") if isinstance(val,
                                                                                                         dict) else "",
                                                              height=68, key=f"t1_{f}_{state_data['state_id']}",
                                                              label_visibility="collapsed", placeholder="输入 Summary")
                                        with inner_cols[1]:
                                            t2 = st.text_area(f"Example",
                                                              value=val.get("example", "") if isinstance(val,
                                                                                                         dict) else "",
                                                              height=68, key=f"t2_{f}_{state_data['state_id']}",
                                                              label_visibility="collapsed", placeholder="输入 Example")

                                        if t1.strip() or t2.strip():
                                            new_props[f] = json.dumps({"summary": t1.strip(), "example": t2.strip()},
                                                                      ensure_ascii=False)
                                    else:
                                        ta = st.text_area(f, value=str(val), height=68,
                                                          key=f"ta_{f}_{state_data['state_id']}",
                                                          label_visibility="collapsed")
                                        if ta.strip():
                                            new_props[f] = _safe_eval(ta.strip())

                            submit_btn = st.form_submit_button("保存状态并覆盖同步")
                            if submit_btn:
                                updated_count = update_state_in_neo4j(entity_name, props, new_props, client)
                                st.toast(f"成功覆盖 {updated_count} 个同源状态节点！", icon="🎉")
                                time.sleep(0.8)
                                st.rerun()

                    prev_props = props

    elif page_mode == "实体关系演进 (Relation)":
        st.subheader("实体双向关系时间线")
        colA, colB = st.columns(2)
        with colA:
            ent_a_raw = st.selectbox("实体 A：", all_entities_list, index=0)
        with colB:
            ent_b_raw = st.selectbox("实体 B：", all_entities_list, index=1 if len(all_entities_list) > 1 else 0)

        if ent_a_raw and ent_b_raw:
            name_a, type_a = ent_a_raw.rsplit(" (", 1)
            type_a = type_a[:-1]
            name_b, type_b = ent_b_raw.rsplit(" (", 1)
            type_b = type_b[:-1]

            if name_a == name_b:
                st.warning("请选择两个不同的实体。")
            else:
                rels = get_deduped_relation_history(name_a, name_b, client)

                if not rels:
                    st.info(f"'{name_a}' 与 '{name_b}' 之间从未产生过直接的关联连线。")
                else:
                    st.success(f"共发现 {len(rels)} 次关系事件。")

                    grouped_rels = {}
                    for rel in rels:
                        ch_id = rel["start_ch_id"]
                        if ch_id not in grouped_rels:
                            grouped_rels[ch_id] = {
                                "ch_name": rel["start_ch_name"],
                                "ch_summary": rel["ch_summary"],
                                "A_to_B": None,
                                "B_to_A": None
                            }
                        grouped_rels[ch_id][rel["direction"]] = rel

                    prev_props_A = {}
                    prev_props_B = {}

                    rel_cfgs = root_running_config._schema.get("relationships", {}).get("custom_relations", {})

                    for ch_id in sorted(grouped_rels.keys()):
                        rel_group = grouped_rels[ch_id]
                        is_last = (ch_id == sorted(grouped_rels.keys())[-1])

                        with st.expander(f"第 {ch_id} 段：{rel_group['ch_name']}", expanded=is_last):
                            st.caption("本段摘要：" + str(rel_group["ch_summary"] or ""))
                            r_col1, r_col2 = st.columns(2)

                            # A -> B 视角渲染

                            with r_col1:
                                st.markdown(f"**[{name_a} ({type_a}) ➔ {name_b}]**")
                                rel_a = rel_group["A_to_B"]
                                if not rel_a:
                                    st.info("本段无此方向关系更新")
                                else:
                                    p_a = rel_a["props"]
                                    r_id_a = rel_a["rel_id"]
                                    with st.form(key=f"f_a_{r_id_a}"):
                                        new_props_a = {}

                                        def get_lbl_a(key, label):
                                            return f"🔴 {label}" if (
                                                    prev_props_A and p_a.get(key) != prev_props_A.get(key)) else label

                                        rel_keys_a = list(p_a.keys())
                                        for r_val in rel_cfgs.values():
                                            if type_a in r_val.get("allowed_sources", []) and type_b in r_val.get(
                                                    "allowed_targets", []):
                                                for k in r_val.get("relation_properties", {}).keys():
                                                    if k not in rel_keys_a:
                                                        rel_keys_a.append(k)

                                        for k in rel_keys_a:
                                            val = st.text_input(get_lbl_a(k, k), value=str(p_a.get(k, "")),
                                                                key=f"a_{k}_{r_id_a}")
                                            if val.strip():
                                                new_props_a[k] = _safe_eval(val.strip())

                                        if st.form_submit_button("保存关系"):
                                            count = update_relation_in_neo4j(name_a, name_b, "A_to_B", p_a, new_props_a,
                                                                             client)
                                            st.toast(f"成功覆盖 {count} 条同源关系线！", icon="🎉")
                                            time.sleep(0.8)
                                            st.rerun()
                                    prev_props_A = p_a

                            # B -> A 视角渲染

                            with r_col2:
                                st.markdown(f"**[{name_b} ({type_b}) ➔ {name_a}]**")
                                rel_b = rel_group["B_to_A"]
                                if not rel_b:
                                    st.info("本段无此方向关系更新")
                                else:
                                    p_b = rel_b["props"]
                                    r_id_b = rel_b["rel_id"]
                                    with st.form(key=f"f_b_{r_id_b}"):
                                        new_props_b = {}

                                        def get_lbl_b(key, label):
                                            return f"🔴 {label}" if (
                                                    prev_props_B and p_b.get(key) != prev_props_B.get(key)) else label

                                        rel_keys_b = list(p_b.keys())
                                        for r_val in rel_cfgs.values():
                                            if type_b in r_val.get("allowed_sources", []) and type_a in r_val.get(
                                                    "allowed_targets", []):
                                                for k in r_val.get("relation_properties", {}).keys():
                                                    if k not in rel_keys_b:
                                                        rel_keys_b.append(k)

                                        for k in rel_keys_b:
                                            val = st.text_input(get_lbl_b(k, k), value=str(p_b.get(k, "")),
                                                                key=f"b_{k}_{r_id_b}")
                                            if val.strip():
                                                new_props_b[k] = _safe_eval(val.strip())

                                        if st.form_submit_button("保存关系"):
                                            count = update_relation_in_neo4j(name_a, name_b, "B_to_A", p_b, new_props_b,
                                                                             client)
                                            st.toast(f"成功覆盖 {count} 条关系线！", icon="🎉")
                                            time.sleep(0.8)
                                            st.rerun()
                                    prev_props_B = p_b
