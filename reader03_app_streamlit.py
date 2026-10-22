import json
import os
import streamlit as st
import pandas as pd
import plotly.express as px
import importlib

import root_running_config
import app.graph.app_graph_config as app_graph_config


@st.cache_data
def load_data():
    ledger_path = app_graph_config.reader03_chunking_node_ledger_path
    segments_path = root_running_config.run_pass1_pipeline_temp_segments_path

    chunks_data = []
    if os.path.exists(ledger_path):
        with open(ledger_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    chunks_data.append(json.loads(line))

    segments_dict = {}
    if os.path.exists(segments_path):
        with open(segments_path, 'r', encoding='utf-8') as f:
            raw_chaps = json.load(f)
            for c in raw_chaps:
                paras = [p.strip() for p in c["text"].split('\n') if p.strip()]
                c_num = c.get("num", c.get("segment_num"))
                segments_dict[c_num] = {
                    "title": c["title"],
                    "paragraphs": paras,
                    "total_paras": len(paras)
                }

    return chunks_data, segments_dict


def render_reader03_page():
    st.title("Reader03 语义切片分布与断点审查")
    st.caption("全局可视化各段切片占比")

    with st.expander("Step 1: 启动切片与向量化引擎", expanded=True):
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            st.info("执行大模型语义断点切割，生成 JSONL 切片账本。")
            if st.button("启动语义切片流水线", use_container_width=True, type="primary"):
                with st.spinner("引擎正在执行切片 (请观测控制台日志)"):
                    importlib.reload(root_running_config)
                    importlib.reload(app_graph_config)
                    from reader03_run_chunking import run_reader03_test
                    run_reader03_test()
                    load_data.clear()
                    st.success("切片流水线流转完毕！")
                    st.rerun()

        with col_btn2:
            st.info("读取账本进行 Embedding 计算并压入 Qdrant 数据库。")
            if st.button("向量化存入 Qdrant", use_container_width=True, type="primary"):
                with st.spinner("向量入库中 (请观测控制台日志)"):
                    import app.database.app_database_config as app_database_config
                    importlib.reload(root_running_config)
                    importlib.reload(app_graph_config)
                    importlib.reload(app_database_config)
                    from reader03_write_qdrant import run_qdrant_write
                    run_qdrant_write()
                    st.success("向量化完毕！")

    chunks_list, segments_dict = load_data()

    if not chunks_list:
        st.warning("暂未检测到切片账本数据，请先运行【语义切片流水线】生成数据！")
        return

    # 建立 节点 -> 切片 的映射
    chap_chunks_map = {}
    for item in chunks_list:
        c_num = item["segment_num"]
        if c_num not in chap_chunks_map:
            chap_chunks_map[c_num] = []
        chap_chunks_map[c_num].append(item)

    for c_num in chap_chunks_map:
        chap_chunks_map[c_num] = sorted(chap_chunks_map[c_num], key=lambda x: x["chunk_index"])

    st.divider()

    st.markdown("### 全局各脉络切点位置与切片占比")

    # 动态获取自定义属性
    custom_payload_fields = app_graph_config.reader03_custom_payload_fields

    plot_data = []
    for c_num in sorted(chap_chunks_map.keys()):
        c_title = segments_dict.get(c_num, {}).get("title", f"第{c_num}段")

        for chunk in chap_chunks_map[c_num]:
            start_id, end_id = map(int, chunk["logical_range"].split('-'))
            para_count = end_id - start_id + 1

            chunk_info = {
                "脉络节点": f"ID {c_num}: {c_title}",
                "切片序号": f"Chunk {chunk['chunk_index']}",
                "段落数": para_count,
                "逻辑范围": chunk["logical_range"],
                "出场实体": "、".join(chunk.get("entities", [])) or "无"
            }

            for k in custom_payload_fields.keys():
                val = chunk.get(k, "")
                if isinstance(val, list):
                    val = "、".join([str(v) for v in val])
                chunk_info[k] = val

            plot_data.append(chunk_info)

    df_plot = pd.DataFrame(plot_data)

    # 动态设置悬浮窗展示字段
    hover_keys = ["出场实体"] + list(custom_payload_fields.keys())

    fig = px.bar(
        df_plot,
        y="脉络节点",
        x="段落数",
        color="切片序号",
        orientation="h",
        text="逻辑范围",
        hover_data=hover_keys,
        title="色块分界线即为【切点】（色块长度代表实际段落数占比）",
        color_discrete_sequence=px.colors.qualitative.Pastel
    )

    fig.update_layout(
        xaxis_title="自然段数量 (节点长度)",
        yaxis_title="",
        height=max(400, len(chap_chunks_map) * 50),
        showlegend=True,
        barmode='stack'
    )
    fig.update_traces(textposition='inside', textfont_size=12)
    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    st.markdown("### 局部切点：人工审查上下文连贯性")
    st.caption("在这里选择具体的切点（相邻两个切片的交界处），核查大模型切分的位置是否符合人类的直觉。")

    col1, col2 = st.columns(2)

    with col1:
        selected_chap_num = st.selectbox(
            "1. 选择要审查的脉络节点：",
            options=sorted(chap_chunks_map.keys()),
            format_func=lambda x: f"ID {x}：{segments_dict.get(x, {}).get('title', '')}"
        )

    current_chap_chunks = chap_chunks_map[selected_chap_num]
    all_paragraphs = segments_dict.get(selected_chap_num, {}).get("paragraphs", [])

    cut_points = []
    for i in range(len(current_chap_chunks) - 1):
        chunk_a = current_chap_chunks[i]
        chunk_b = current_chap_chunks[i + 1]
        cut_start_id = int(chunk_b["logical_range"].split('-')[0])
        cut_points.append({
            "id": i,
            "label": f"切点 {i + 1} (Chunk {chunk_a['chunk_index']} → Chunk {chunk_b['chunk_index']} | 切在第 {cut_start_id} 段)",
            "chunk_a": chunk_a,
            "chunk_b": chunk_b,
            "cut_start_id": cut_start_id
        })

    with col2:
        if not cut_points:
            st.info("大模型判断只有一个切片，不存在切点。")
            selected_cut = None
        else:
            selected_cut_id = st.selectbox(
                "2. 选择要核查的切点：",
                options=[cp["id"] for cp in cut_points],
                format_func=lambda x: cut_points[x]["label"]
            )
            selected_cut = cut_points[selected_cut_id]

    if selected_cut:
        cut_start_id = selected_cut["cut_start_id"]
        st.markdown(f"#### 正在审查：【第 {cut_start_id} 段】附近的上下文流转")

        col_left, col_right = st.columns(2)

        with col_left:
            st.markdown(f"##### 上文：属于 Chunk {selected_cut['chunk_a']['chunk_index']}")
            # 用循环动态渲染所有的自定义字段
            for k in custom_payload_fields.keys():
                val = selected_cut['chunk_a'].get(k, "")
                if isinstance(val, list): val = "、".join([str(v) for v in val])
                st.info(f"**{k}**：{val}")

            prev_start = max(1, cut_start_id - 4)
            prev_end = cut_start_id - 1
            if prev_start <= prev_end:
                for p_id in range(prev_start, prev_end + 1):
                    if p_id - 1 < len(all_paragraphs):
                        st.caption(f"**[{p_id}]** {all_paragraphs[p_id - 1]}")

        with col_right:
            st.markdown(f"##### 下文：属于 Chunk {selected_cut['chunk_b']['chunk_index']}")
            # 同理动态渲染下文
            for k in custom_payload_fields.keys():
                val = selected_cut['chunk_b'].get(k, "")
                if isinstance(val, list): val = "、".join([str(v) for v in val])
                st.success(f"**{k}**：{val}")

            if cut_start_id - 1 < len(all_paragraphs):
                st.error(f"**切点核心 [第 {cut_start_id} 段]** \n\n {all_paragraphs[cut_start_id - 1]}")

            next_start = cut_start_id + 1
            next_end = min(len(all_paragraphs), cut_start_id + 3)
            if next_start <= next_end:
                for p_id in range(next_start, next_end + 1):
                    if p_id - 1 < len(all_paragraphs):
                        st.caption(f"**[{p_id}]** {all_paragraphs[p_id - 1]}")
