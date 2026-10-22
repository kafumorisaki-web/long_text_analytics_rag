import streamlit as st
from langchain_core.messages import HumanMessage
from app.graph.workflow import compiled_graph


def render_analyzer_page():
    st.title("Analyzer 检索引擎与对话")
    st.caption("基于图谱与向量双路架构的长文本推理大脑（请自定义）")

    config = {"configurable": {"thread_id": "analyzer_chat_session"}}

    state_snapshot = compiled_graph.get_state(config)
    messages = state_snapshot.values.get("messages", []) if state_snapshot.values else []

    for msg in messages:
        role = "user" if isinstance(msg, HumanMessage) else "assistant"
        with st.chat_message(role):
            st.markdown(msg.content)

    if prompt := st.chat_input("向 Analyzer 提问..."):
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Analyzer 正在推演..."):
                result_state = compiled_graph.invoke({
                    "skip_readers": True,
                    "messages": [HumanMessage(content=prompt)]
                }, config=config)

                # 获取尾部最新回复并渲染
                latest_msg = result_state["messages"][-1].content
                st.markdown(latest_msg)
