import root_running_config

"""nodes.py"""
reader01_node_read_segments_path = root_running_config.run_pass1_pipeline_temp_segments_path
reader01_node_validated_payloads_path = root_running_config.run_pass1_pipeline_validated_payloads_path
reader01_node_entity_types_config = root_running_config.reader01_entity_types_config
reader01_node_custom_properties = root_running_config._schema.get("segment_nodes", {}).get("schema", {}).get("custom_properties", {})
reader01_macro_config = root_running_config._schema.get("macro_nodes", {})
reader01_macro_ledger_path = root_running_config.run_pass1_pipeline_macro_ledger_path

reader02_entity_classes_config = root_running_config._schema.get("entity_system", {}).get("entity_classes", {})
reader02_custom_relations_config = root_running_config._schema.get("relationships", {}).get("custom_relations", {})
reader02_main_entities_path = root_running_config.final_approved_main_path
reader02_minor_entities_path = root_running_config.final_approved_minor_path
reader02_get_segment_main_entities_clean_ledger_path = root_running_config.run_pass1_pipeline_clean_ledger
reader02_state_evaluator_node_tmp_path = "./data/temp_reader02_state_ledger.json"
reader02_relation_evaluator_node_tmp_path = "./data/temp_reader02_relation_ledger.json"
reader02_state_whole_ledger_fixed_path = "./data/reader02_state_ledger.jsonl"
reader02_relation_whole_ledger_fixed_path = "./data/reader02_relation_ledger.jsonl"

# 每个切片的预计长度
reader03_chunking_node_length_per_chunks = 500
# 每个切片在本段的长度占比下限（切片段落数/本段总段落数）
reader03_chunking_node_min_length_ratio_per_chunks = 0.05
# 每个切片在本段的长度占比上限（切片段落数/本段总段落数）
reader03_chunking_node_max_length_ratio_per_chunks = 0.90
# 每个切片上下文重叠的前/后百分比（按段落数）
reader03_chunking_node_expand_offset_per_chunks = 0.15
# 每段至少有多少个切片
reader03_chunking_node_static_min_chunks = 2
reader03_chunking_node_ledger_path = "./data/reader03_semantic_chunks_ledger.jsonl"

reader03_custom_payload_fields = root_running_config._schema.get("vector_system", {}).get("payload_schema", {}).get("custom_payload_fields", {})
reader03_meta_vector_components = root_running_config._schema.get("vector_system", {}).get("vector_strategy", {}).get("meta_vector_components", ["entities"])

"""workflow.py"""
graph_sqlite_db_path = root_running_config.graph_sqlite_db_path

reader02_router_test_segment_num = 130
reader03_router_test_segment_num = 130
