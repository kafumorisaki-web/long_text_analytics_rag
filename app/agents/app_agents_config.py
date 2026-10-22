import root_running_config

"""llm_client.py"""
llm_log_dir = "./logs/llm_traces"

# 不知为何有时候litellm会疯狂打印provider list，设为true即可屏蔽。默认应false
litellm_suppress_debug_info_flag = True

sample_reader01_model = "openrouter/deepseek/deepseek-v3.2"
sample_reader01_model_provider_order = ["DeepInfra"]

sample_reader01_summarizer_model = "openrouter/deepseek/deepseek-v3.2"
sample_reader01_summarizer_model_provider_order = ["DeepInfra"]

sample_reader02_model = "openrouter/google/gemini-3.7-flash"
sample_reader02_model_provider_order = ["google-vertex"]

sample_reader03_model = "openrouter/openai/gpt-5.6-luna"
sample_reader03_model_provider_order = ["openai"]

embedding_reader03_model = "openrouter/openai/text-embedding-3-small"

sample_analyzer_model = "openrouter/openai/gpt-5.6-luna"
sample_analyzer_model_provider_order = ["openai"]

"""tools.py"""
macro_custom_props = root_running_config._schema.get("macro_nodes", {}).get("schema", {}).get("custom_properties", {})
segment_custom_props = root_running_config._schema.get("segment_nodes", {}).get("schema", {}).get("custom_properties", {})
entity_classes_config = root_running_config._schema.get("entity_system", {}).get("entity_classes", {})
custom_relations_config = root_running_config._schema.get("relationships", {}).get("custom_relations", {})
custom_payload_fields = root_running_config._schema.get("vector_system", {}).get("payload_schema", {}).get("custom_payload_fields", {})
