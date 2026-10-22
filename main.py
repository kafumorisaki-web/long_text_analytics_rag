import streamlit as st
from reader01_app_streamlit import render_reader01_page
from reader02_app_streamlit import render_reader02_page
from reader03_app_streamlit import render_reader03_page
from analyzer_app_streamlit import render_analyzer_page

# 全局页面配置必须放在所有 Streamlit 指令的最前面
st.set_page_config(page_title="Graph-Vector Hybrid RAG Engine", layout="wide", page_icon="⚙️")

st.sidebar.title("解析引擎控制台")
page = st.sidebar.radio(
    "导航菜单",
    [
        "1. Reader01 提取与清洗",
        "2. Reader02 状态与关系",
        "3. Reader03 切片与向量化",
        "4. Analyzer 检索引擎与对话"
    ]
)

st.sidebar.markdown("---")
st.sidebar.caption("Graph-Vector Hybrid RAG v2.0")

# 纯粹的条件路由
if page == "1. Reader01 提取与清洗":
    render_reader01_page()
elif page == "2. Reader02 状态与关系":
    render_reader02_page()
elif page == "3. Reader03 切片与向量化":
    render_reader03_page()
elif page == "4. Analyzer 检索引擎与对话":
    render_analyzer_page()
