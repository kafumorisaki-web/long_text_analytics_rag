import re
import json
from dotenv import load_dotenv, find_dotenv
import root_running_config
from app.graph.workflow import compiled_graph
from app.utils.logger import sys_logger
from collections import Counter, defaultdict

load_dotenv(find_dotenv())


def parse_full_text(file_path: str):
    with open(file_path, 'r', encoding='utf-8') as f:
        full_text = f.read()

    pattern = root_running_config.split_pattern
    parts = re.split(pattern, full_text)

    segments = []
    # parts[0] 通常是序言或空白，从 index 1 开始，步长为 2
    segment_counter = 1
    for i in range(1, len(parts) - 1, 2):
        raw_title = parts[i].strip()
        clean_title = clean_text(raw_title, is_title=True)

        content = parts[i + 1].strip().replace("\u3000", "").replace("\n\n", "\n")
        if len(content) > 50:
            segments.append({
                "num": segment_counter,
                "title": clean_title,
                "text": content
            })
            segment_counter += 1

    return segments


def clean_text(text: str, is_title=False) -> str:
    if not text: return ""
    if is_title:
        text = re.sub(root_running_config.title_clean_bracket_pattern, "", text)
        prefix_pattern = root_running_config.title_clean_prefix_pattern
        text = re.sub(prefix_pattern, "", text)
        return text.strip()

    # 实体名采用绝对白名单
    return re.sub(root_running_config.entity_name_clean_pattern, "", text).strip()


def post_process_and_analyze(raw_ledger_path: str, clean_ledger_path: str):

    cleaned_records = []
    # 使用 defaultdict 自动为不同类型的实体创建独立的计数器
    entity_counters = defaultdict(Counter)
    segment_titles = []

    with open(raw_ledger_path, 'r', encoding='utf-8') as f:
        for line in f:
            if not line.strip(): continue
            payload = json.loads(line)

            raw_title = payload.get("segment_title", "")
            clean_title = clean_text(raw_title, is_title=True)
            payload["segment_title"] = clean_title
            segment_titles.append(clean_title)

            entities = payload.get("extracted_entities", [])
            for ent in entities:
                raw_name = ent.get("raw_name", "")
                clean_name = clean_text(raw_name)
                # 原地覆盖
                ent["raw_name"] = clean_name
                ent_type = ent.get("type", "其他")
                if clean_name:
                    # 将清洗后的名字追加到对应分类的计数器中
                    entity_counters[ent_type][clean_name] += 1

            cleaned_records.append(payload)

    # 存入干净的账本供下一步 Neo4j 使用
    with open(clean_ledger_path, 'w', encoding='utf-8') as f:
        for rec in cleaned_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    return segment_titles, entity_counters


def run_pass1_pipeline():
    text_path = root_running_config.run_pass1_pipeline_text_path
    temp_segments_path = root_running_config.run_pass1_pipeline_temp_segments_path
    segments = parse_full_text(text_path)

    with open(temp_segments_path, 'w', encoding='utf-8') as f:
        json.dump(segments, f, ensure_ascii=False)
    sys_logger.info(f"正则解析完毕，共切割出 {len(segments)} 段。静态切片已缓存。")


    config = root_running_config.run_pass1_pipeline_thread_config

    state_snapshot = compiled_graph.get_state(config)

    # 判断是否需要从头跑一遍，无视之前已经生成多少，默认false
    reset_flag = root_running_config.run_pass1_pipeline_reset_flag

    # 检查状态机中是否已存在 reader01_total_segments_count 字段来判断是否为历史断点
    if state_snapshot and state_snapshot.values and "reader01_total_segments_count" in state_snapshot.values and not reset_flag:
        resume_index = state_snapshot.values.get("reader01_current_index", 0)
        sys_logger.warning(f"检测到 SQLite 中存在历史断点，正在从第 {resume_index + 1} 段恢复自动流转...")
        # 传入 None 触发 LangGraph 从 Checkpointer 读取最后状态恢复
        compiled_graph.invoke(None, config=config)
    else:
        if reset_flag:
            sys_logger.warning("手动设置reset_flag为True，重新生成reader01所有解析")
        else:
            sys_logger.info("初始化状态并交给 LangGraph 控制")
        open(root_running_config.run_pass1_pipeline_validated_payloads_path, 'w').close()
        open(root_running_config.run_pass1_pipeline_macro_ledger_path, 'w').close()
        initial_state = {
            "reader01_total_segments_count": len(segments),
            "reader01_current_index": 0,
            "reader01_macro_task_index": 0,
            "reader01_previous_segment_summary": "这是第一段，无前情梗概。"
        }
        # 引擎将initial_state作为当前状态（初始状态）开始运行
        compiled_graph.invoke(initial_state, config=config)

    sys_logger.info(f"结果已固化至: {root_running_config.run_pass1_pipeline_validated_payloads_path}")

    sys_logger.info(f"\n提取流结束。开始进入后处理与数据洗仓...")
    post_process_and_analyze(
        root_running_config.run_pass1_pipeline_validated_payloads_path,
        root_running_config.run_pass1_pipeline_clean_ledger
    )


if __name__ == "__main__":
    run_pass1_pipeline()
