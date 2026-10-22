import json


system_schema_config = "./system_schema_config.json"
try:
    with open(system_schema_config, 'r', encoding='utf-8') as f:
        _schema = json.load(f)
except FileNotFoundError:
    raise RuntimeError(f"致命错误：未找到系统配置文件，请确保文件存在。")
except json.JSONDecodeError as e:
    raise RuntimeError(f"致命错误：系统配置文件 JSON 格式损坏。报错信息: {e}")


reader01_entity_types_config = {
    entity_key: entity_val.get("extraction_description", "")
    for entity_key, entity_val in _schema.get("entity_system", {}).get("entity_classes", {}).items()
}
_parsing_rules = _schema.get("segment_nodes", {}).get("parsing_rules", {})
segment_business_name = _parsing_rules.get("segment_business_name", "")
split_pattern = _parsing_rules.get("split_pattern", "")
title_clean_bracket_pattern = _parsing_rules.get("title_clean_bracket_pattern", "")
title_clean_prefix_pattern = _parsing_rules.get("title_clean_prefix_pattern", "")
entity_name_clean_pattern = _parsing_rules.get("entity_name_clean_pattern", "")

run_pass1_pipeline_text_path = "data/raw/text.txt"
run_pass1_pipeline_thread_config = {"configurable": {"thread_id": "reader01_test_run"}}
run_pass1_pipeline_temp_segments_path = "data/reader01_split_raw_segments.json"
run_pass1_pipeline_validated_payloads_path = "data/reader01_staging_ledger_lines.jsonl"
graph_sqlite_db_path = "data/langgraph_checkpoints.db"
run_pass1_pipeline_reset_flag = False
run_pass1_pipeline_clean_ledger = "data/reader01_staging_ledger_lines_cleaned.jsonl"
run_pass1_pipeline_macro_ledger_path = "data/reader01_macro_summary.jsonl"

"""reader01_app_streamlit.py, reader01_write_neo4j.py"""
final_approved_main_path = "data/reader01_approved_main_entities.json"
final_approved_minor_path = "data/reader01_approved_minor_entities.json"

"""reader02_run_evolution.py"""
run_reader02_test_thread_config = {"configurable": {"thread_id": "reader02_formal_run"}}
run_reader02_test_skip_reader01_flag = True

"""reader03_run_chunking.py"""
run_reader03_test_thread_config = {"configurable": {"thread_id": "reader03_formal_run"}}
run_reader03_test_skip_reader01_flag = True
run_reader03_test_skip_reader02_flag = True