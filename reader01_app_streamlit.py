import streamlit as st
import json
import pandas as pd
import plotly.express as px
from collections import defaultdict, Counter
import os
import root_running_config
import reader01_write_neo4j
import importlib
import app.graph.app_graph_config
from app.utils.document_parser import UniversalDocumentParser


def render_reader01_page():
    st.title("Reader01 宏观提纯与实体审计")
    st.markdown("本模块负责切分原始文本，提炼段落事实（Segment Summary），并按规则提取当前登场实体。")

    # ==========================================
    # 上传与挂载
    # ==========================================
    with st.expander("Step 1: 上传与挂载原文", expanded=True):
        uploaded_file = st.file_uploader("请上传长文本数据", type=['txt', 'pdf', 'docx', 'epub'])
        if uploaded_file is not None:
            save_path = getattr(root_running_config, "run_pass1_pipeline_text_path", "../data/text.txt")
            os.makedirs(os.path.dirname(save_path), exist_ok=True)

            with st.spinner("正在启动多格式文本提纯网关，强行解包为标准 UTF-8 纯净段落..."):
                try:
                    file_bytes = uploaded_file.read()

                    clean_text = UniversalDocumentParser.parse_file(file_bytes, uploaded_file.name)

                    with open(save_path, "w", encoding="utf-8") as f:
                        f.write(clean_text)

                    st.success(f"提纯成功！共提取 {len(clean_text)} 字符。纯文本挂载至: {save_path}")
                except Exception as e:
                    st.error(f"文本提纯网关执行失败，无法解包该文件: {str(e)}")

    # ==========================================
    # 全局 Schema 规则配置 (动态覆写 JSON)
    # ==========================================
    with st.expander("Step 2: 全局推演 Schema 终控台", expanded=True):
        config_path = root_running_config.system_schema_config
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)

            st.caption(
                "在此定义整个推演引擎的物理法则。表格点击最左侧空白处选中行后，按 Delete 键可删除。带有 * 的列为必填项。")

            # 渲染只读的系统硬编码字段
            def render_hardcoded_table(schema_dict):
                hardcoded = {k: v for k, v in schema_dict.items() if k != "custom_properties"}
                if hardcoded:
                    df = pd.DataFrame([
                        {"系统硬编码字段 (不可改)": k, "数据类型": v.get("data_type", ""),
                         "底层描述 (内部使用)": v.get("description", "")}
                        for k, v in hardcoded.items()
                    ])
                    st.dataframe(df, use_container_width=True, hide_index=True)

            # 渲染带有 Pydantic 强约束的自定义表格
            def render_pydantic_editor(properties_dict, editor_key, extra_cols_data=None, extra_cols_config=None):
                rows = []
                for k, v in properties_dict.items():
                    options_val = v.get("options", [])
                    options_str = ",".join(options_val) if isinstance(options_val, list) else ""

                    row_data = {
                        "字段名(Key)*": k,
                        "数据类型*": v.get("data_type", "str"),
                        "大模型提取指令(Description)*": v.get("description", ""),
                        "最大长度": v.get("max_length", None),
                        "默认值(Default)": str(v.get("default", "")) if v.get("default") is not None else "",
                        "枚举项(Options)": options_str
                    }
                    if extra_cols_data and k in extra_cols_data:
                        row_data.update(extra_cols_data[k])
                    rows.append(row_data)

                df = pd.DataFrame(rows)
                # 初始化空表头
                empty_cols = ["字段名(Key)*", "数据类型*", "大模型提取指令(Description)*", "最大长度",
                              "默认值(Default)", "枚举项(Options)"]
                if extra_cols_config:
                    empty_cols = list(extra_cols_config.keys()) + empty_cols
                if df.empty:
                    df = pd.DataFrame(columns=empty_cols)

                base_column_config = {
                    "字段名(Key)*": st.column_config.TextColumn(required=True),
                    "数据类型*": st.column_config.SelectboxColumn(options=["str", "int", "list[str]", "enum"],
                                                                  required=True),
                    "大模型提取指令(Description)*": st.column_config.TextColumn(required=True),
                    "最大长度": st.column_config.NumberColumn(min_value=1, step=1),
                    "默认值(Default)": st.column_config.TextColumn(),
                    "枚举项(Options)": st.column_config.TextColumn(help="若类型为enum必填，用英文逗号分隔")
                }
                if extra_cols_config:
                    base_column_config.update(extra_cols_config)

                edited_df = st.data_editor(
                    df, num_rows="dynamic", use_container_width=True, key=editor_key, column_config=base_column_config
                )

                new_props = {}
                extra_returns = {}
                for _, row in edited_df.iterrows():
                    k = str(row["字段名(Key)*"]).strip()
                    if not k or k == "nan": continue

                    data_type = str(row["数据类型*"])
                    prop = {"data_type": data_type, "description": str(row["大模型提取指令(Description)*"]).strip()}
                    if pd.notna(row["最大长度"]): prop["max_length"] = int(row["最大长度"])
                    if row["默认值(Default)"] and str(row["默认值(Default)"]).strip() != "nan": prop["default"] = str(
                        row["默认值(Default)"]).strip()
                    if data_type == "enum" and row["枚举项(Options)"]:
                        opts_str = str(row["枚举项(Options)"])
                        if opts_str.strip() and opts_str != "nan":
                            prop["options"] = [opt.strip() for opt in opts_str.split(",") if opt.strip()]

                    new_props[k] = prop
                    if extra_cols_config:
                        extra_returns[k] = {col: row[col] for col in extra_cols_config.keys()}

                return new_props, extra_returns

            # ================== UI 分区展示 ==================
            tab1, tab2, tab3 = st.tabs(["1. 脉络解析与附属切片", "2. 实体与状态演进", "3. 宏观归纳与关系线"])

            new_seg_props = {}
            new_entity_classes = {}
            new_macro_props_nested = {}
            new_relations = {}

            new_vec_payload_props = {}
            new_meta_components = ["entities"]

            # ================== 脉络 ==================
            with tab1:
                st.markdown("#### 基础切分规则")
                parsing_rules = config_data.get("segment_nodes", {}).get("parsing_rules", {})
                new_business_name = st.text_input("脉络业务称呼 (如'章'或'月度')",
                                                  value=parsing_rules.get("segment_business_name", ""))
                new_split_pattern = st.text_input("1. 全文切分正则 (split_pattern)",
                                                  value=parsing_rules.get("split_pattern", ""))
                new_clean_bracket = st.text_input("2. 标题清括号正则 (title_clean_bracket_pattern)",
                                                  value=parsing_rules.get("title_clean_bracket_pattern", ""))
                new_clean_prefix = st.text_input("3. 标题清前缀正则 (title_clean_prefix_pattern)",
                                                 value=parsing_rules.get("title_clean_prefix_pattern", ""))
                new_clean_entity = st.text_input("4. 实体名去非法字符正则 (entity_name_clean_pattern)",
                                                 value=parsing_rules.get("entity_name_clean_pattern", ""))

                st.markdown("---")
                st.markdown("#### 脉络附属切片元数据 (Segment Custom Properties)")
                seg_props = config_data.get("segment_nodes", {}).get("schema", {}).get("custom_properties", {})
                new_seg_props, _ = render_pydantic_editor(seg_props, "segment_props_editor")

                # 直接挂在 Tab 1 底部的 Qdrant 切片与向量配置
                st.markdown("---")
                st.markdown("#### 向量切片负载与合成配方 (Qdrant Vector System)")
                vec_sys = config_data.get("vector_system", {})
                vec_payload_props = vec_sys.get("payload_schema", {}).get("custom_payload_fields", {})
                new_vec_payload_props, _ = render_pydantic_editor(vec_payload_props, "vector_payload_props_editor")

                # 动态计算可选的 Meta Vector ：强制保底 "entities" + 当前自定义的 Payload 字段
                available_meta_keys = ["entities"] + [k for k in new_vec_payload_props.keys() if k != "entities"]
                old_meta_comp = vec_sys.get("vector_strategy", {}).get("meta_vector_components", ["entities"])
                # 过滤掉已经被删除的失效字段，并确保 entities 必在其中
                valid_default_meta = list(
                    dict.fromkeys(["entities"] + [c for c in old_meta_comp if c in available_meta_keys]))

                selected_meta_comp = st.multiselect(
                    "Meta Vector 向量合成组合 (meta_vector_components) — 'entities' 为必填项",
                    options=available_meta_keys,
                    default=valid_default_meta,
                    key="meta_vec_comp_selector"
                )

                new_meta_components = ["entities"] + [c for c in selected_meta_comp if c != "entities"]

            # ================== 实体与状态 ==================
            with tab2:
                st.markdown("#### 实体白名单库 (Entity Classes)")
                entity_classes = config_data.get("entity_system", {}).get("entity_classes", {})

                # 顶层实体增删改查表
                ent_rows = [{"实体分类标识(Key)*": k, "提取定义(Description)*": v.get("extraction_description", "")} for
                            k, v in entity_classes.items()]
                ent_df = pd.DataFrame(ent_rows) if ent_rows else pd.DataFrame(
                    columns=["实体分类标识(Key)*", "提取定义(Description)*"])
                edited_ent_df = st.data_editor(
                    ent_df, num_rows="dynamic", use_container_width=True, key="ent_class_list_editor",
                    column_config={"实体分类标识(Key)*": st.column_config.TextColumn(required=True),
                                   "提取定义(Description)*": st.column_config.TextColumn(required=True)}
                )

                st.markdown("---")
                st.markdown("#### 实体下属状态演进追踪 (State Properties)")
                st.caption("为上方注册的实体类配置需要追踪的动态属性。")

                for _, row in edited_ent_df.iterrows():
                    ent_key = str(row["实体分类标识(Key)*"]).strip()
                    if not ent_key or ent_key == "nan": continue
                    desc = str(row["提取定义(Description)*"]).strip()

                    with st.container(border=True):
                        st.subheader(f"{ent_key} 状态属性配置")
                        old_states = entity_classes.get(ent_key, {}).get("state_properties", {})
                        new_states, _ = render_pydantic_editor(old_states, f"state_editor_{ent_key}")
                        new_entity_classes[ent_key] = {"extraction_description": desc, "state_properties": new_states}

            # ================== 归纳与关系线 ==================
            with tab3:
                st.markdown("#### 宏观归纳映射 (Macro Nodes Aggregation)")
                st.caption("定义全周期级别的归纳任务。必须指定其“底层原料”来源于脉络节点的哪个字段。")

                # 扁平化读取嵌套字典
                macro_props = config_data.get("macro_nodes", {}).get("schema", {}).get("custom_properties", {})
                flat_macro_props = {}
                extra_macro_data = {}
                for src_field, tgt_dict in macro_props.items():
                    for tgt_field, tgt_prop in tgt_dict.items():
                        flat_macro_props[tgt_field] = tgt_prop
                        extra_macro_data[tgt_field] = {"底层原料字段(Source)*": src_field}

                macro_extra_col = {"底层原料字段(Source)*": st.column_config.TextColumn(required=True,
                                                                                        help="对应 Segment Nodes 里的属性名，如 segment_summary")}
                new_flat_macros, returned_extras = render_pydantic_editor(flat_macro_props, "macro_props_editor",
                                                                          extra_macro_data, macro_extra_col)

                # 重新打包为嵌套字典
                for tgt_field, prop in new_flat_macros.items():
                    src_field = returned_extras[tgt_field]["底层原料字段(Source)*"].strip()
                    if src_field not in new_macro_props_nested: new_macro_props_nested[src_field] = {}
                    new_macro_props_nested[src_field][tgt_field] = prop

                st.markdown("---")
                st.markdown("#### 归纳节点实例划分 (Macro Instances)")
                st.caption("定义具体的归纳节点名及包含的段落起止范围。")
                instances_data = config_data.get("macro_nodes", {}).get("instances", [])
                inst_rows = []
                for inst in instances_data:
                    rng = inst.get("segment_range", [])
                    inst_rows.append({
                        "节点排序(ID)*": inst.get("macro_id", 0),
                        "归纳节点名称*": inst.get("macro_name", ""),
                        "脉络起始ID*": rng[0] if len(rng) > 0 else None,
                        "脉络结束ID*": rng[1] if len(rng) > 1 else None
                    })
                inst_df = pd.DataFrame(inst_rows) if inst_rows else pd.DataFrame(
                    columns=["节点排序(ID)*", "归纳节点名称*", "脉络起始ID*", "脉络结束ID*"])

                edited_inst_df = st.data_editor(
                    inst_df, num_rows="dynamic", use_container_width=True, key="macro_instances_editor",
                    column_config={
                        "节点排序(ID)*": st.column_config.NumberColumn(required=True, min_value=1, step=1),
                        "归纳节点名称*": st.column_config.TextColumn(required=True),
                        "脉络起始ID*": st.column_config.NumberColumn(required=True, min_value=1, step=1),
                        "脉络结束ID*": st.column_config.NumberColumn(required=True, min_value=1, step=1)
                    }
                )

                st.markdown("---")
                st.markdown("#### 交互关系网络组 (Relations)")
                relations = config_data.get("relationships", {}).get("custom_relations", {})

                # 顶层关系组增删改查表
                rel_rows = []
                for k, v in relations.items():
                    rel_rows.append({
                        "关系组标识(Key)*": k,
                        "发起方(Sources)*": ",".join(v.get("allowed_sources", [])),
                        "接收方(Targets)*": ",".join(v.get("allowed_targets", []))
                    })
                rel_df = pd.DataFrame(rel_rows) if rel_rows else pd.DataFrame(
                    columns=["关系组标识(Key)*", "发起方(Sources)*", "接收方(Targets)*"])
                edited_rel_df = st.data_editor(
                    rel_df, num_rows="dynamic", use_container_width=True, key="rel_group_list_editor",
                    column_config={
                        "关系组标识(Key)*": st.column_config.TextColumn(required=True),
                        "发起方(Sources)*": st.column_config.TextColumn(required=True, help="逗号分隔的实体Key"),
                        "接收方(Targets)*": st.column_config.TextColumn(required=True)
                    }
                )

                st.markdown("##### 关系组详细属性 (Relation Properties)")
                for _, row in edited_rel_df.iterrows():
                    rel_key = str(row["关系组标识(Key)*"]).strip()
                    if not rel_key or rel_key == "nan": continue
                    srcs = [s.strip() for s in str(row["发起方(Sources)*"]).split(",") if s.strip()]
                    tgts = [t.strip() for t in str(row["接收方(Targets)*"]).split(",") if t.strip()]

                    with st.container(border=True):
                        st.subheader(f"{rel_key} 属性配置")
                        old_rel_props = relations.get(rel_key, {}).get("relation_properties", {})
                        new_rel_props, _ = render_pydantic_editor(old_rel_props, f"rel_props_{rel_key}")
                        new_relations[rel_key] = {"allowed_sources": srcs, "allowed_targets": tgts,
                                                  "relation_properties": new_rel_props}

            if st.button("全局保存 Schema 配置", type="primary", use_container_width=True):

                config_data["segment_nodes"]["parsing_rules"]["segment_business_name"] = new_business_name
                config_data["segment_nodes"]["parsing_rules"]["split_pattern"] = new_split_pattern
                config_data["segment_nodes"]["parsing_rules"]["title_clean_bracket_pattern"] = new_clean_bracket
                config_data["segment_nodes"]["parsing_rules"]["title_clean_prefix_pattern"] = new_clean_prefix
                config_data["segment_nodes"]["parsing_rules"]["entity_name_clean_pattern"] = new_clean_entity
                config_data["segment_nodes"]["schema"]["custom_properties"] = new_seg_props

                config_data["entity_system"]["entity_classes"] = new_entity_classes

                config_data["macro_nodes"]["schema"]["custom_properties"] = new_macro_props_nested
                config_data["relationships"]["custom_relations"] = new_relations

                # 组装 Instances 列表
                new_instances = []
                for _, row in edited_inst_df.iterrows():
                    m_id = str(row["节点排序(ID)*"]).strip()
                    if not m_id or m_id == "nan": continue
                    new_instances.append({
                        "macro_id": int(float(m_id)),
                        "macro_name": str(row["归纳节点名称*"]).strip(),
                        "segment_range": [int(float(row["脉络起始ID*"])), int(float(row["脉络结束ID*"]))]
                    })
                config_data["macro_nodes"]["instances"] = new_instances

                if "vector_system" not in config_data:
                    config_data["vector_system"] = {"payload_schema": {}, "vector_strategy": {}}
                config_data["vector_system"].setdefault("payload_schema", {})[
                    "custom_payload_fields"] = new_vec_payload_props
                config_data["vector_system"].setdefault("vector_strategy", {})[
                    "meta_vector_components"] = new_meta_components

                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(config_data, f, ensure_ascii=False, indent=2)
                st.toast("全局 Schema 协议已安全覆写！")
                st.balloons()

        except Exception as e:
            st.error(f"读取或解析配置文件失败: {e}")

    # ==========================================
    # 启动引擎
    # ==========================================
    with st.expander("Step 3: 启动解析引擎", expanded=True):
        st.info("点击启动后，系统将按照配置切割原文并调用 LLM 逐段提取事实。请确保后台 Terminal 处于可见状态以观测日志。")

        is_reset = st.checkbox("强制重置（抹除当前线程的所有历史断点，从第 1 段重新跑）", value=False)

        if st.button("一键执行 Reader01 Pipeline", type="primary", use_container_width=True):
            with st.spinner("引擎高负荷运转中，请观察后台控制台日志，切勿刷新页面..."):
                try:
                    # 强制系统重新读取 JSON 配置文件并覆盖内存
                    importlib.reload(root_running_config)
                    importlib.reload(app.graph.app_graph_config)
                    # 动态覆写内存中的全局变量，这样 run_pass1_pipeline 就能读到了
                    root_running_config.run_pass1_pipeline_reset_flag = is_reset
                    # 动态导入，避免循环依赖，只有点击按钮时才加载跑批脚本
                    from reader01_run_ingestion import run_pass1_pipeline
                    run_pass1_pipeline()
                    st.cache_data.clear()
                    st.success("Reader01 全量解析任务圆满完成！请展开 Step 4 进行清洗。")
                except Exception as e:
                    st.error(f"解析任务异常崩溃: {e}")

    # ==========================================
    # 统计与人工清洗
    # ==========================================
    with st.expander("Step 4: 数据统计与实体清洗台", expanded=True):
        # 通过判断“干净账本”或“原始有效载荷”是否存在，决定是否渲染图表
        payload_path = root_running_config.run_pass1_pipeline_validated_payloads_path

        if not os.path.exists(payload_path):
            st.warning("暂无解析数据。请先在 Step 3 中完成文本解析引擎的运行。")
        else:
            st.success("检测到已落盘的解析账本")

            # 使用 st.cache_data 确保只在第一次启动时读取硬盘
            @st.cache_data
            def load_and_count_entities(clean_ledger_path: str):
                entity_counters = defaultdict(Counter)
                if not os.path.exists(clean_ledger_path):
                    return entity_counters

                with open(clean_ledger_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        if not line.strip(): continue
                        payload = json.loads(line)
                        for ent in payload.get("extracted_entities", []):
                            # 兼容不同键名，并提取分类
                            name = ent.get("raw_name", "")
                            ent_type = ent.get("type", "其他")
                            if name:
                                entity_counters[ent_type][name] += 1

                return entity_counters

            st.title("Reader01: 实体频率分析与人工干预台")

            # 数据源路径
            LEDGER_PATH = root_running_config.run_pass1_pipeline_clean_ledger
            # 主要实体
            FINAL_APPROVED_PATH = root_running_config.final_approved_main_path
            # 次要实体
            FINAL_MINOR_PATH = root_running_config.final_approved_minor_path

            raw_counters = load_and_count_entities(LEDGER_PATH)

            if not raw_counters:
                st.error(f"找不到清洗后的账本数据：{LEDGER_PATH}，请先运行 Pass 1 流水线。")
                st.stop()

            categories = list(raw_counters.keys())

            if "overrides" not in st.session_state:
                st.session_state.overrides = {}

            for cat in categories:

                if cat not in st.session_state.overrides or "major_add" not in st.session_state.overrides[cat]:
                    st.session_state.overrides[cat] = {
                        "major_add": [], "major_remove": [],
                        "minor_add": [], "minor_remove": []
                    }

            tabs = st.tabs(categories)

            final_approved_dict = {}
            final_minor_dict = {}

            for i, category in enumerate(categories):
                with tabs[i]:
                    st.subheader(f"【{category}】实体管理")

                    counter = raw_counters[category]
                    all_entities = list(counter.keys())

                    if not all_entities:
                        st.info("该类别无实体提取记录。")
                        continue

                    max_freq = max(counter.values())
                    min_freq = min(counter.values())

                    slider_max = max_freq if max_freq > min_freq else min_freq + 1

                    st.markdown("### 第一重：主要实体界定")
                    col_major_ctrl, col_major_plot = st.columns([1.2, 3])

                    with col_major_ctrl:
                        default_major_min = max(min_freq, int(max_freq * 0.1)) if max_freq > 10 else min_freq
                        major_range = st.slider(
                            "主要实体频次区间", min_value=min_freq, max_value=slider_max,
                            value=(default_major_min, max_freq), step=1, key=f"major_slider_{category}"
                        )

                        major_baseline = set(e for e, c in counter.items() if major_range[0] <= c <= major_range[1])

                        major_added = st.multiselect(
                            "🟢 强制提拔为主要实体", options=all_entities,
                            default=st.session_state.overrides[category]["major_add"], key=f"major_add_{category}"
                        )

                        major_removed = st.multiselect(
                            "🟡 从主要实体中剔除", options=all_entities,
                            default=st.session_state.overrides[category]["major_remove"], key=f"major_remove_{category}"
                        )

                        st.session_state.overrides[category]["major_add"] = major_added
                        st.session_state.overrides[category]["major_remove"] = major_removed

                        final_major_set = (major_baseline | set(major_added)) - set(major_removed)
                        st.metric("最终主要实体数量", len(final_major_set))

                    with col_major_plot:
                        major_plot_data = [{"实体": e, "频次": counter[e]} for e in final_major_set]
                        if major_plot_data:
                            df_major = pd.DataFrame(major_plot_data).sort_values(by=["频次", "实体"],
                                                                                 ascending=[False, True])
                            fig_major = px.bar(
                                df_major, x="实体", y="频次", text_auto=True,
                                color="频次", color_continuous_scale=px.colors.sequential.Blues
                            )
                            fig_major.update_layout(xaxis_tickangle=-45, height=300, margin=dict(b=50, t=10))
                            st.plotly_chart(fig_major, use_container_width=True)
                        else:
                            st.warning("暂无主要实体。")

                    st.divider()

                    st.markdown("### 第二重：次要实体界定")

                    # 动态算出当前未能进入主要名单的剩余实体
                    remaining_for_minor = list(set(all_entities) - final_major_set)

                    col_minor_ctrl, col_minor_plot = st.columns([1.2, 3])

                    with col_minor_ctrl:
                        minor_range = st.slider(
                            "次要实体频次区间", min_value=min_freq, max_value=slider_max,
                            value=(min_freq, max(min_freq, default_major_min - 1)), step=1,
                            key=f"minor_slider_{category}"
                        )

                        # 从全集中筛选符合区间要求的，同时绝对排斥已经成为主要实体的元素
                        minor_baseline = set(e for e, c in counter.items() if minor_range[0] <= c <= minor_range[1])
                        minor_baseline = minor_baseline - final_major_set

                        minor_added = st.multiselect(
                            "🟢 强制提拔为次要实体", options=remaining_for_minor,
                            default=[x for x in st.session_state.overrides[category]["minor_add"] if
                                     x in remaining_for_minor], key=f"minor_add_{category}"
                        )

                        minor_removed = st.multiselect(
                            "🔴 从次要实体中剔除", options=remaining_for_minor,
                            default=[x for x in st.session_state.overrides[category]["minor_remove"] if
                                     x in remaining_for_minor], key=f"minor_remove_{category}"
                        )

                        st.session_state.overrides[category]["minor_add"] = minor_added
                        st.session_state.overrides[category]["minor_remove"] = minor_removed

                        final_minor_set = (minor_baseline | set(minor_added)) - set(minor_removed) - final_major_set
                        st.metric("最终次要实体数量", len(final_minor_set))

                    with col_minor_plot:
                        minor_plot_data = [{"实体": e, "频次": counter[e]} for e in final_minor_set]
                        if minor_plot_data:
                            df_minor = pd.DataFrame(minor_plot_data).sort_values(by=["频次", "实体"],
                                                                                 ascending=[False, True])
                            fig_minor = px.bar(
                                df_minor, x="实体", y="频次", text_auto=True,
                                color="频次", color_continuous_scale=px.colors.sequential.Oranges
                            )
                            fig_minor.update_layout(xaxis_tickangle=-45, height=300, margin=dict(b=50, t=10))
                            st.plotly_chart(fig_minor, use_container_width=True)
                        else:
                            st.info("暂无次要实体。")

                    st.divider()

                    st.markdown("### 彻底剔除的无意义实体")

                    # 全集 - 主要 - 次要
                    final_trash_set = set(all_entities) - final_major_set - final_minor_set

                    if final_trash_set:
                        st.error("以下实体将被直接剔除，不进入任何知识库：\n\n" + " 、 ".join(list(final_trash_set)))
                    else:
                        st.success("暂无被剔除的实体。")

                    # 赋值回全局字典以供最终保存落盘
                    final_approved_dict[category] = list(final_major_set)
                    final_minor_dict[category] = list(final_minor_set)

            st.markdown("---")
            col_empty, col_save = st.columns([4, 2])
            with col_save:
                if st.button("确认并生成主/次两份实体清单", type="primary", use_container_width=True):
                    with open(FINAL_APPROVED_PATH, 'w', encoding='utf-8') as f:
                        json.dump(final_approved_dict, f, ensure_ascii=False, indent=2)

                    with open(FINAL_MINOR_PATH, 'w', encoding='utf-8') as f:
                        json.dump(final_minor_dict, f, ensure_ascii=False, indent=2)

                    st.toast(f"生成主要白名单: {FINAL_APPROVED_PATH}")
                    st.toast(f"生成次要实体库: {FINAL_MINOR_PATH}")
                    st.balloons()

            st.markdown("### Step 5: 实体图谱构建")
            if st.button("将全量数据压入图数据库 (Neo4j)", type="primary", use_container_width=True):
                with st.spinner("正在建立时空轨道与实体锚点..."):
                    try:
                        reader01_write_neo4j.main()
                        st.success("图谱构建完毕！Reader01 流程全线通关！")
                    except Exception as e:
                        st.error(f"图谱写入失败: {e}")

            pass
