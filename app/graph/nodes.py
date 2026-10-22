import json
import os
import time
from app.graph.state import EngineState
from app.agents.llm_client import LLMClient
from app.agents.prompts import (
    READER01_SYSTEM_PROMPT,
    READER01_MACRO_SUMMARIZER_SYSTEM_PROMPT,
    READER02_STATE_SYSTEM_PROMPT,
    READER02_RELATIONSHIP_SYSTEM_PROMPT,
    READER03_CHUNKING_SYSTEM_PROMPT,
    ANALYZER_SYSTEM_PROMPT
)
import app.graph.app_graph_config as app_graph_config
from typing import Dict, Any
from app.utils.logger import sys_logger
from app.models.schemas import (
    get_reader01_dynamic_schema,
    get_macro_dynamic_schema_partial,
    get_reader02_state_dynamic_schema,
    get_reader02_relation_dynamic_schema,
    get_reader03_dynamic_schema
)
from app.database.neo4j_services import Reader02GraphService
import atexit
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

llm_client = LLMClient()

_global_graph_service = None


def get_graph_service() -> Reader02GraphService:
    global _global_graph_service
    if _global_graph_service is None:
        _global_graph_service = Reader02GraphService()
    return _global_graph_service


@atexit.register
def _auto_cleanup_neo4j():
    global _global_graph_service
    if _global_graph_service is not None:
        _global_graph_service.close()
        _global_graph_service = None
        sys_logger.info("Neo4j 连接池释放")


def reader01_node(state: EngineState) -> Dict[str, Any]:
    # 提取当前状态内存中的原料
    idx = state.get("reader01_current_index", 0)

    with open(app_graph_config.reader01_node_read_segments_path, 'r', encoding='utf-8') as f:
        all_segments = json.load(f)

    sg = all_segments[idx]
    segment_num = sg["num"]
    segment_title = sg["title"]
    raw_text = sg["text"]
    prev_summary = state.get("reader01_previous_segment_summary", "无前文梗概。")
    sys_logger.debug(f"当前状态内存:{[segment_num, raw_text, prev_summary]}")
    sys_logger.info(
        f"[LangGraph 节点执行] 正在审计第 {segment_num}/{state.get('reader01_total_segments_count')} 段: {sg['title']}")

    # 读取实体类型与自定义属性配置
    entity_types_config = app_graph_config.reader01_node_entity_types_config
    valid_entity_keys = list(entity_types_config.keys())

    # 通过 app_graph_config 代理获取 custom_properties
    custom_props = app_graph_config.reader01_node_custom_properties

    # 传入 custom_props 渲染出动态模型
    DynamicSegmentPayload = get_reader01_dynamic_schema(valid_entity_keys, custom_props)

    # 动态渲染 System Prompt
    types_desc_str = "\n".join([f"- 【{k}】: {v}" for k, v in entity_types_config.items()])
    # 将 JSON 里的描述转化为给 LLM 看的文本指令
    custom_desc_str = "\n".join([f"- 【{k}】: {v.get('description', '')}" for k, v in custom_props.items()])
    if not custom_desc_str:
        custom_desc_str = "无附加自定义维度。"
    system_prompt = READER01_SYSTEM_PROMPT.format(
        entity_types_description=types_desc_str,
        custom_properties_descriptions=custom_desc_str
    )

    # 组装用户侧 Prompt
    base_user_prompt = f"""
    【前一段梗概参考】: {prev_summary}

    【当前需要审计的第 {segment_num} 段原文开始】:
    {raw_text}
    【本段原文结束】

    请严格按照提取原则，输出本段的结构化审计事实。
    """

    # 标准 Pydantic 对象
    # 容错重试机制
    max_retries = 3
    last_exception = None
    # 动态游标，初始等于基础 Prompt
    current_user_prompt = base_user_prompt
    for attempt in range(max_retries):
        try:
            validated_payload = llm_client.sample_reader01(
                segment_num=segment_num,
                system_prompt=system_prompt,
                user_prompt=current_user_prompt,
                response_model=DynamicSegmentPayload
            )
            payload_dict = validated_payload.model_dump(mode="json")

            payload_dict["segment_num"] = segment_num
            payload_dict["segment_title"] = segment_title

            # 每成功提取一段，直接变成一行独立的 JSON 写入最终账本。
            with open(app_graph_config.reader01_node_validated_payloads_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(payload_dict, ensure_ascii=False) + "\n")

            sys_logger.info(f"第 {segment_num} 段提取成功")

            # 将提取结果返回全局共享状态
            return {
                "current_segment_num": segment_num,
                "reader01_previous_segment_summary": payload_dict.get("segment_summary", ""),
                "reader01_current_index": idx + 1
            }

        except Exception as e:
            last_exception = e
            sys_logger.error(f"第 {segment_num} 段第 {attempt} 次提取失败，报错信息: {e}")
            if attempt < max_retries - 1:
                current_user_prompt = base_user_prompt + f"""\n
                                【系统严重警告】：你上一次的输出未通过严格的JSON结构校验。
                                Pydantic报错信息如下：
                                {str(e)}

                                请务必阅读报错信息，修正缺失或类型错误的字段，重新输出合法的JSON！
                                """
                time.sleep(2)

                # 若 3 次重试全部失败，抛出异常挂起程序。
    # 状态已在上一段完结时由 Checkpointer 序列化存入 SQLite
    raise RuntimeError(
        f"第 {segment_num} 段连续 {max_retries} 次解析失败，错误原因: {last_exception}")


def _get_macro_tasks() -> list:
    """将嵌套的归纳任务物理展平为一维队列"""
    macro_config = app_graph_config.reader01_macro_config
    macro_instances = macro_config.get("instances", [])
    custom_props = macro_config.get("schema", {}).get("custom_properties", {})

    tasks = []
    for inst in macro_instances:
        for src_field, tgt_dict in custom_props.items():
            tasks.append({
                "macro_id": inst.get("macro_id", 1),
                "macro_name": inst.get("macro_name", f"归纳节点_{inst.get('macro_id', 1)}"),
                "start_id": inst.get("segment_range", [1, -1])[0],
                "end_id": inst.get("segment_range", [1, -1])[1],
                "src_field": src_field,
                "tgt_dict": tgt_dict
            })
    return tasks


def reader01_macro_summarizer_node(state: EngineState) -> Dict[str, Any]:
    task_idx = state.get("reader01_macro_task_index", 0)
    tasks = _get_macro_tasks()

    # 越界保护
    if task_idx >= len(tasks):
        return {}

    current_task = tasks[task_idx]
    macro_id = current_task["macro_id"]
    macro_name = current_task["macro_name"]
    start_id = current_task["start_id"]
    end_id = current_task["end_id"]
    src_field = current_task["src_field"]
    tgt_dict = current_task["tgt_dict"]

    sys_logger.info(
        f"\n[LangGraph] 正在执行 Macro 归纳子任务 {task_idx + 1}/{len(tasks)}: [{macro_name}] 提取原料 [{src_field}]...")

    ledger_path = app_graph_config.reader01_node_validated_payloads_path
    target_segments = []
    if os.path.exists(ledger_path):
        with open(ledger_path, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip(): continue
                seg = json.loads(line)
                if start_id <= seg.get("segment_num", 0) <= end_id:
                    target_segments.append(seg)

    raw_material_text = ""
    has_data = False
    for seg in target_segments:
        val = seg.get(src_field)
        if val and (not isinstance(val, list) or len(val) > 0):
            has_data = True
            if isinstance(val, list): val = "、".join(val)
            raw_material_text += f"[第{seg.get('segment_num')}段]: {val}\n"

    if not has_data:
        sys_logger.info(f"Macro [{macro_name}] 维度 [{src_field}] 无可用原料，静默跳过。")
        empty_res = {"macro_id": macro_id}
        for k in tgt_dict.keys(): empty_res[k] = None

        macro_ledger_path = app_graph_config.reader01_macro_ledger_path
        with open(macro_ledger_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(empty_res, ensure_ascii=False) + "\n")
        return {"reader01_macro_task_index": task_idx + 1}

    DynamicMacroPartialSchema = get_macro_dynamic_schema_partial(tgt_dict)

    instructions = []
    for tgt_key, prop in tgt_dict.items():
        instructions.append(f"- 目标属性【{tgt_key}】：{prop.get('description', '')}")

    system_prompt = READER01_MACRO_SUMMARIZER_SYSTEM_PROMPT.format(
        src_field=src_field,
        macro_instructions="\n".join(instructions)
    )
    base_user_prompt = f"【底层脉络：{src_field}】\n{raw_material_text}"

    max_retries = 3
    last_exception = None
    current_user_prompt = base_user_prompt

    for attempt in range(max_retries):
        try:
            validated_payload = llm_client.sample_reader01_macro_summarizer(
                macro_name=f"{macro_name} | {src_field}",
                system_prompt=system_prompt,
                user_prompt=current_user_prompt,
                response_model=DynamicMacroPartialSchema
            )
            # 拿到局部结果，打上主键
            partial_result = validated_payload.model_dump(mode="json")
            partial_result["macro_id"] = macro_id

            with open(app_graph_config.reader01_macro_ledger_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(partial_result, ensure_ascii=False) + "\n")

            sys_logger.info(f"子任务 {task_idx + 1}/{len(tasks)} 完成，数据已追加。")
            break
        except Exception as e:
            last_exception = e
            if attempt < max_retries - 1:
                current_user_prompt = base_user_prompt + f"\n【系统警告】：结构校验失败。Pydantic报错:\n{str(e)}\n请立刻修正！"
                time.sleep(2)
    else:
        raise RuntimeError(f"Macro [{macro_name}] 提取 [{src_field}] 连续失败。报错: {last_exception}")

    return {"reader01_macro_task_index": task_idx + 1}


def _reader02_get_segment_main_entities(segment_num: int) -> list:
    """计算当前段出场实体与全局主要实体的交集"""
    segment_entities = set()
    with open(app_graph_config.reader02_get_segment_main_entities_clean_ledger_path, 'r', encoding='utf-8') as f:
        for line in f:
            if not line.strip(): continue
            data = json.loads(line)
            if data.get("segment_num") == segment_num:
                segment_entities = {ent.get("raw_name") for ent in data.get("extracted_entities", []) if
                                    ent.get("raw_name")}
                break
    with open(app_graph_config.reader02_main_entities_path, 'r', encoding='utf-8') as f:
        main_entities_dict = json.load(f)
    global_main_entities = {ent for sublist in main_entities_dict.values() for ent in sublist}

    return list(segment_entities & global_main_entities)


def reader02_state_evaluator_node(state: EngineState) -> Dict[str, Any]:
    """
    Reader02 - 节点 1：判定实体内在状态演进
    """

    idx = state.get("reader02_current_index", 0)

    with open(app_graph_config.reader01_node_read_segments_path, 'r', encoding='utf-8') as f:
        all_segments = json.load(f)

    sg = all_segments[idx]
    segment_num = sg["num"]
    raw_text = sg["text"]

    sys_logger.info(
        f"\n[LangGraph 节点执行] Reader02 开始推演第 {segment_num}/{len(all_segments)} 段实体状态...")

    # sys_logger.debug(f"本段内容是：{raw_text}")

    active_entities = _reader02_get_segment_main_entities(segment_num)

    temp_path = app_graph_config.reader02_state_evaluator_node_tmp_path
    entity_classes_cfg = app_graph_config.reader02_entity_classes_config

    # 如果本段没有任何白名单主要实体出场，直接拦截
    if not active_entities:
        sys_logger.info(f"第 {segment_num} 段无主要实体出场，跳过状态提取。")
        empty_payload = {"segment_num": segment_num, **{k: [] for k in entity_classes_cfg.keys()}}
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(empty_payload, f, ensure_ascii=False)

        return {}

    # 实时从 Neo4j 数据库拉取“已知历史状态”

    graph_service = get_graph_service()
    prev_summary = graph_service.get_previous_segment_summary(segment_num)
    latest_states = graph_service.get_entities_latest_states(active_entities)

    sys_logger.debug(f"提取出的最新状态{latest_states}")

    DynamicStateSchema = get_reader02_state_dynamic_schema(entity_classes_cfg)
    state_instr_lines = []
    for ent_key, ent_val in entity_classes_cfg.items():
        state_props = ent_val.get("state_properties", {})
        if not state_props:
            continue
        state_instr_lines.append(f"【实体类别：{ent_key}】")
        for p_key, p_val in state_props.items():
            state_instr_lines.append(f"  - {p_key}: {p_val.get('description', '')}")
    state_tracking_instructions = "\n".join(state_instr_lines) if state_instr_lines else "无特定追踪属性。"

    system_prompt = READER02_STATE_SYSTEM_PROMPT.format(
        state_tracking_instructions=state_tracking_instructions
    )

    base_user_prompt = f"""
        【前情脉络摘要】
        {prev_summary}
        
        【本段活跃主要实体名单】
        {json.dumps(active_entities, ensure_ascii=False)}
        
        【当前文本切片】
        {raw_text}
    
        【已知历史最新状态】
        {json.dumps(latest_states, ensure_ascii=False, indent=2)}
        请严格按照增量推演铁律，比对当前文本切片与已知历史最新状态：
            1. 根据实体的 'entity_type' 将发生状态突变的实体归入对应的类别列表中。
            2. 未发生实质性变化的属性必须置为 null，未发生任何属性变化的实体严禁输出。
        """

    sys_logger.debug(f"组装好的base_user_prompt长度: {len(base_user_prompt)}")
    sys_logger.debug(f"检查：上一段的summary是{prev_summary}")

    max_retries = 3
    last_exception = None
    current_user_prompt = base_user_prompt

    for attempt in range(max_retries):
        try:

            validated_payload = llm_client.sample_reader02(
                segment_num=segment_num,
                system_prompt=system_prompt,
                user_prompt=current_user_prompt,
                response_model=DynamicStateSchema,
                agent_role="reader02_state"
            )

            payload_dict = validated_payload.model_dump(mode="json")

            payload_dict["segment_num"] = segment_num

            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(payload_dict, f, ensure_ascii=False, indent=2)

            sys_logger.info(f"第 {segment_num} 段状态增量已覆盖写入临时状态账本{temp_path}")

            return {}

        except Exception as e:
            last_exception = e
            sys_logger.error(f"第 {segment_num} 段 State 提取第 {attempt} 次失败: {e}")
            if attempt < max_retries - 1:
                current_user_prompt = base_user_prompt + f"""\n
                                【系统严重警告】：上一次输出未通过JSON校验。
                                Pydantic报错信息：
                                {str(e)}
                                请修正报错，重新输出合法的JSON！
                                """
                time.sleep(2)

    raise RuntimeError(f"第 {segment_num} 段 State 解析连续 {max_retries} 次失败。错误: {last_exception}")


def reader02_relation_evaluator_node(state: EngineState) -> Dict[str, Any]:
    """
    Reader02 - 节点 2：判定实体拓扑连线演进
    """
    idx = state.get("reader02_current_index", 0)

    with open(app_graph_config.reader01_node_read_segments_path, 'r', encoding='utf-8') as f:
        all_segments = json.load(f)

    segment_num = all_segments[idx]["num"]
    raw_text = all_segments[idx]["text"]

    sys_logger.info(f"[LangGraph 节点执行] Reader02 开始推演第 {segment_num} 段关系网络...")

    # sys_logger.debug(f"本段内容是：{raw_text}")

    active_entities = _reader02_get_segment_main_entities(segment_num)
    temp_path = app_graph_config.reader02_relation_evaluator_node_tmp_path
    custom_relations_cfg = app_graph_config.reader02_custom_relations_config

    if len(active_entities) < 2:
        sys_logger.info(f"第 {segment_num} 段出场主要实体不足 2 个，跳过关系提取。")
        empty_payload = {"segment_num": segment_num, **{k: [] for k in custom_relations_cfg.keys()}}
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(empty_payload, f, ensure_ascii=False)
        return {}

    graph_service = get_graph_service()
    prev_summary = graph_service.get_previous_segment_summary(segment_num)
    latest_relations = graph_service.get_entities_latest_relations(active_entities)

    sys_logger.debug(f"提取出的最新关系{latest_relations}")

    DynamicRelationSchema = get_reader02_relation_dynamic_schema(custom_relations_cfg)

    rel_instr_lines = []
    for rel_key, rel_val in custom_relations_cfg.items():
        srcs = rel_val.get("allowed_sources", [])
        tgts = rel_val.get("allowed_targets", [])
        rel_props = rel_val.get("relation_properties", {})
        rel_instr_lines.append(f"【关系组：{rel_key}】（允许发起方类型: {srcs} -> 允许接收方类型: {tgts}）")
        for p_key, p_val in rel_props.items():
            rel_instr_lines.append(f"  - {p_key}: {p_val.get('description', '')}")
    relation_tracking_instructions = "\n".join(rel_instr_lines) if rel_instr_lines else "无特定关系追踪规则。"

    system_prompt = READER02_RELATIONSHIP_SYSTEM_PROMPT.format(
        relation_tracking_instructions=relation_tracking_instructions
    )

    base_user_prompt = f"""
        【前情脉络摘要】
        {prev_summary}
        
        【本段活跃主要实体名单】
        {json.dumps(active_entities, ensure_ascii=False)}
        
        【当前文本切片】
        {raw_text}

        【已知历史最新关系】
        {json.dumps(latest_relations, ensure_ascii=False, indent=2)}
        """

    sys_logger.debug(f"组装好的base_user_prompt长度: {len(base_user_prompt)}")

    max_retries = 3
    last_exception = None
    current_user_prompt = base_user_prompt

    for attempt in range(max_retries):
        try:
            validated_payload = llm_client.sample_reader02(
                segment_num=segment_num,
                system_prompt=system_prompt,
                user_prompt=current_user_prompt,
                response_model=DynamicRelationSchema,
                agent_role="reader02_relation"
            )
            payload_dict = validated_payload.model_dump(mode="json")
            payload_dict["segment_num"] = segment_num

            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(payload_dict, f, ensure_ascii=False, indent=2)

            sys_logger.info(f"第 {segment_num} 段关系增量已覆盖写入临时关系账本")

            return {}

        except Exception as e:
            last_exception = e
            sys_logger.error(f"⚠️ 第 {segment_num} 段 Relation 提取第 {attempt} 次失败: {e}")
            if attempt < max_retries - 1:
                current_user_prompt = base_user_prompt + f"""\n
                                【系统严重警告】：上一次输出未通过JSON校验。
                                Pydantic报错信息：
                                {str(e)}
                                请修正报错，重新输出合法的JSON！
                                """
                time.sleep(2)

    raise RuntimeError(f"第 {segment_num} 段 Relation 解析连续 {max_retries} 次失败。错误: {last_exception}")


def reader02_neo4j_writer_node(state: EngineState) -> dict:
    """
    Reader02 - 节点 3：数据提取与结算中枢
    """
    idx = state.get("reader02_current_index", 0)

    state_path = app_graph_config.reader02_state_evaluator_node_tmp_path
    relation_path = app_graph_config.reader02_relation_evaluator_node_tmp_path

    with open(state_path, 'r', encoding='utf-8') as f:
        state_delta = json.load(f)
    with open(relation_path, 'r', encoding='utf-8') as f:
        relation_delta = json.load(f)

    segment_num = state_delta.get("segment_num")
    sys_logger.info(f"[LangGraph 节点执行] Reader02 结算C: 开始将第 {segment_num} 段挂载至图谱与账本...")

    with open(app_graph_config.reader02_state_whole_ledger_fixed_path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(state_delta, ensure_ascii=False) + "\n")
    with open(app_graph_config.reader02_relation_whole_ledger_fixed_path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(relation_delta, ensure_ascii=False) + "\n")

    graph_service = get_graph_service()
    try:
        graph_service.execute_segment_updates(segment_num, state_delta, relation_delta)
        sys_logger.info(f"第 {segment_num} 段图谱锚点更新完毕。")
    except Exception as e:
        sys_logger.error(f"Neo4j 入库失败: {e}")
        raise

    return {
        "reader02_current_index": idx + 1
    }


def reader03_chunking_node(state: EngineState) -> Dict[str, Any]:
    """
    Reader03 - 语义切片节点
    """
    idx = state.get("reader03_current_index", 0)

    segments_path = app_graph_config.reader01_node_read_segments_path
    if not os.path.exists(segments_path):
        sys_logger.error(f"找不到数据文件: {segments_path}")
        return {}

    with open(segments_path, 'r', encoding='utf-8') as f:
        all_segments = json.load(f)

    if idx >= len(all_segments):
        sys_logger.info("Reader03 所有段落语义切片处理完毕")
        return {}

    sg = all_segments[idx]
    segment_num = sg["num"]
    raw_text = sg["text"]

    sys_logger.info(f"\n[Reader03 语义切片] 正在处理第 {segment_num} 段 (总进度: {idx + 1}/{len(all_segments)})...")

    # 按自然段切分并格式化为带标号文本：[1] 第一段 \n [2] 第二段
    raw_paragraphs = [p.strip() for p in raw_text.split('\n') if p.strip()]
    total_paragraphs = len(raw_paragraphs)
    if total_paragraphs == 0:
        sys_logger.warning(f"第 {segment_num} 段为空，直接跳过。")
        return {"reader03_current_index": idx + 1}

    numbered_text = "\n".join([f"[{i + 1}] {p}" for i, p in enumerate(raw_paragraphs)])

    # sys_logger.debug(f"numbered_text: {numbered_text}")

    # 动态计算切片数量上下限 (以 500 字为一个基准 Chunk)
    text_len = sum(len(p) for p in raw_paragraphs)
    base_chunks = max(app_graph_config.reader03_chunking_node_static_min_chunks,
                      text_len // app_graph_config.reader03_chunking_node_length_per_chunks)
    min_chunks = max(app_graph_config.reader03_chunking_node_static_min_chunks, base_chunks - 1)
    max_chunks = base_chunks + 1

    sys_logger.debug(f"min_chunks: {min_chunks}; max_chunks: {max_chunks}")

    # 加载主要/次要实体白名单
    global_approved_entities = set()
    for path in [
        app_graph_config.reader02_main_entities_path,
        app_graph_config.reader02_minor_entities_path
    ]:
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    ent_dict = json.load(f)
                    for ent_list in ent_dict.values():
                        global_approved_entities.update(ent_list)
            except Exception as e:
                sys_logger.warning(f"读取实体白名单 {path} 失败: {e}")

    # 提取本段特有属性
    segment_extracted_entities = set()
    prev_summary = "无前情提要"
    clean_ledger_path = app_graph_config.reader02_get_segment_main_entities_clean_ledger_path

    if os.path.exists(clean_ledger_path):
        try:
            with open(clean_ledger_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("segment_num") == segment_num:
                        prev_summary = data.get("segment_summary", "无")
                        # 提取本段 Reader01 识别出的所有实体
                        for ent in data.get("extracted_entities", []):
                            raw_name = ent.get("raw_name")
                            if raw_name:
                                segment_extracted_entities.add(raw_name)
                        break
        except Exception as e:
            sys_logger.warning(f"读取实体白名单失败: {e}")

    # 计算物理交集：本段合法候选实体 = 全局白名单 ∩ 本段识别出的实体
    valid_entities_list = list(global_approved_entities.intersection(segment_extracted_entities))

    sys_logger.debug(f"valid_entities_list: {valid_entities_list}")

    custom_payload_fields = app_graph_config.reader03_custom_payload_fields

    DynamicResponseModel = get_reader03_dynamic_schema(
        valid_entities_list,
        custom_payload_fields,
        min_chunks,
        max_chunks
    )

    instr_lines = []
    for k, v in custom_payload_fields.items():
        instr_lines.append(f"- {k}: {v.get('description', '')}")
    custom_chunk_fields_instructions = "\n".join(instr_lines) if instr_lines else "无附加自定义字段。"

    system_prompt = READER03_CHUNKING_SYSTEM_PROMPT.format(
        custom_chunk_fields_instructions=custom_chunk_fields_instructions
    )

    base_user_prompt = f"""
    【前情脉络摘要】
    {prev_summary}
    
    【本段合法实体白名单候选】
    {json.dumps(valid_entities_list, ensure_ascii=False)}

    【待切分原文】
    {numbered_text}
    """

    max_retries = 8
    last_exception = None
    current_user_prompt = base_user_prompt

    for attempt in range(max_retries):
        try:
            sys_logger.info(f"正在尝试第 {attempt + 1}/{max_retries} 次调用大模型切片...")

            validated_payload = llm_client.sample_reader03(
                segment_num=segment_num,
                system_prompt=system_prompt,
                user_prompt=current_user_prompt,
                response_model=DynamicResponseModel,
                agent_role="reader03_chunker"
            )

            chunks_data = validated_payload.chunks

            sys_logger.debug(f"validated_payload: {validated_payload}")

            # --- 物理逻辑断点校验 1：首个切片必须从第 1 段开始 ---
            if chunks_data[0].start_paragraph_id != 1:
                raise ValueError(
                    f"切片段落标号校验失败：首个切片的 start_paragraph_id 必须等于 1，但上一次输出的是 {chunks_data[0].start_paragraph_id}。"
                )

            # 校验 2 & 3：递增、越界，以及 5%-90% 的合理性校验
            for i in range(len(chunks_data)):
                start_id = chunks_data[i].start_paragraph_id

                if i > 0:
                    prev_id = chunks_data[i - 1].start_paragraph_id
                    if start_id <= prev_id:
                        raise ValueError(
                            f"切片段落标号校验失败：第 {i + 1} 个切片的起始标号 ({start_id}) 未严格大于前一个切片的标号 ({prev_id})。"
                        )
                    if start_id > total_paragraphs:
                        raise ValueError(
                            f"切片段落标号校验失败：第 {i + 1} 个切片的起始标号 ({start_id}) 超出了本段总段落数 ({total_paragraphs})。"
                        )

                # 提前计算当前块的理论结束段落
                if i < len(chunks_data) - 1:
                    end_id = chunks_data[i + 1].start_paragraph_id - 1
                else:
                    end_id = total_paragraphs

                chunk_len = end_id - start_id + 1
                ratio = chunk_len / total_paragraphs

                if ratio < app_graph_config.reader03_chunking_node_min_length_ratio_per_chunks:
                    min_required_paras = max(3,
                                             int(total_paragraphs * app_graph_config.reader03_chunking_node_min_length_ratio_per_chunks) + 1)
                    raise ValueError(
                        f"单个切片长度太短：第 {i + 1} 个切片仅包含 {chunk_len} 段（段落 {start_id} - {end_id}），占总段落的 {ratio * 100:.1f}%，切分太过细碎！\n"
                        f"该切片至少需要包含 {min_required_paras} 个自然段。请更换此切片标号（现为 {start_id}）， 或更换下一个切片的起始段标号（现为{end_id + 1}），或向前/向后合并细碎切片，禁止start_paragraph_id {start_id}和{end_id + 1}同时输出！"
                    )
                if ratio > app_graph_config.reader03_chunking_node_max_length_ratio_per_chunks:
                    max_allowed_paras = int(
                        total_paragraphs * app_graph_config.reader03_chunking_node_max_length_ratio_per_chunks)
                    raise ValueError(
                        f"单个切片长度太长：第 {i + 1} 个切片包含 {chunk_len} 段（段落 {start_id} - {end_id}），占总段落的 {ratio * 100:.1f}%，切分太过臃肿！\n"
                        f"该切片最多只能包含 {max_allowed_paras} 个自然段。请更换此切片标号（现为 {start_id}），或更换下一个切片的起始段标号（现为{end_id + 1}），或在中间增加切点，禁止start_paragraph_id {start_id}和{end_id + 1}同时输出！"
                    )

            # 计算物理 Overlap
            final_chunks = []

            sys_logger.debug(
                f"第 {segment_num} 段校验通过！总段落: {total_paragraphs} 段, 净字数: {text_len} 字。开始构建带重叠的物理切片...")

            for i, chunk in enumerate(chunks_data):
                start_id = chunk.start_paragraph_id
                if i < len(chunks_data) - 1:
                    end_id = chunks_data[i + 1].start_paragraph_id - 1
                else:
                    end_id = total_paragraphs

                # 按当前切片的段落总数计算 15% 的前后扩张量
                chunk_length = end_id - start_id + 1
                expand_offset = max(1,
                                    int(chunk_length * app_graph_config.reader03_chunking_node_expand_offset_per_chunks))

                sys_logger.debug(f"expand_offset: {expand_offset}")

                physical_start = max(1, start_id - expand_offset)
                physical_end = min(total_paragraphs, end_id + expand_offset)

                sys_logger.debug(
                    f"当前切片上下文重叠后的范围：physical_start: {physical_start}; physical_end: {physical_end}")

                # 按物理段落索引抓取包含重叠的原文 (自然段标号转为 0-based 数组索引)
                chunk_text = "\n".join(raw_paragraphs[physical_start - 1: physical_end])

                sys_logger.debug(
                    f"当前{i + 1}切片字数：{len(chunk_text)}")

                chunk_dict = chunk.model_dump(mode="json")

                payload = {
                    "segment_num": segment_num,
                    "chunk_index": i + 1,
                    "logical_range": f"{start_id}-{end_id}",
                    "physical_range": f"{physical_start}-{physical_end}",
                    "entities": chunk_dict.get("entities", []),
                    "chunk_text": chunk_text
                }

                for custom_k in custom_payload_fields.keys():
                    payload[custom_k] = chunk_dict.get(custom_k)

                final_chunks.append(payload)

            ledger_path = app_graph_config.reader03_chunking_node_ledger_path
            os.makedirs(os.path.dirname(ledger_path), exist_ok=True)

            with open(ledger_path, 'a', encoding='utf-8') as f:
                for fc in final_chunks:
                    f.write(json.dumps(fc, ensure_ascii=False) + "\n")

            sys_logger.info(
                f"第 {segment_num} 段切片成功，生成 {len(final_chunks)} 个语义切片，已追加写入至: {ledger_path}")

            return {"reader03_current_index": idx + 1}

        except Exception as e:
            last_exception = e
            sys_logger.error(f"第 {segment_num} 段切片第 {attempt + 1} 次尝试失败: {e}")

            if attempt < max_retries:
                current_user_prompt = current_user_prompt + f"""\n
                                【系统第 {attempt + 1} 次错误警告】
                                你上一次的输出触发系统硬性拦截，报错如下：
                                {str(e)}
                                必须解决此问题，否则将被系统无限物理拦截导致无法输出，没有任何价值！请修正报错，重新调整!
                                """
                time.sleep(2)

    raise RuntimeError(f"第 {segment_num} 段切片连续 {max_retries} 次尝试全部失败，最后一次报错: {last_exception}")


def analyzer_node(state: EngineState) -> Dict[str, Any]:
    """
    Analyzer 极简空壳节点。完全摘去旧版 Planner 复杂的图内子路由，利用单刀直入的模型通讯进行推理。
    """
    sys_logger.info("\n[LangGraph] 引擎流转至 Analyzer 中枢节点...")

    messages = state.get("messages", [])
    cursor = state.get("analyzer_cursor", 0)

    if not messages:
        sys_logger.warning("[Analyzer] 消息总线为空")
        return {}

    try:

        decision = llm_client.sample_analyzer(
            messages=messages,
            system_prompt=ANALYZER_SYSTEM_PROMPT
        )

        sys_logger.info(f"[Analyzer Thought]: {decision.thought}")
        sys_logger.info(f"[Analyzer Response]: {decision.response}")

        # 利用 reducer 机制，将 AI 的纯文本回复封装进 AIMessage 尾部追加
        return {
            "analyzer_cursor": cursor + 1,
            "messages": [AIMessage(content=decision.response)]
        }

    except Exception as e:
        sys_logger.error(f"Analyzer 节点执行异常: {e}")
        # 防止图谱宕机，强行返回一条报错作为消息体
        return {
            "messages": [AIMessage(content=f"引擎处于宕机状态，异常详情：{str(e)}")]
        }
