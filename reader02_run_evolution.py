from dotenv import load_dotenv, find_dotenv
import root_running_config
from app.graph.workflow import compiled_graph
from app.utils.logger import sys_logger

load_dotenv(find_dotenv())


def run_reader02():
    config = root_running_config.run_reader02_test_thread_config

    state_snapshot = compiled_graph.get_state(config)

    # 如果快照存在，且下一步 (next) 有挂起的节点 (比如被 interrupt_before 拦住的节点)
    if state_snapshot and state_snapshot.next:
        sys_logger.info(f"检测到引擎挂起在 {state_snapshot.next}，正在推进 Reader02...")
        # 传入 None，释放挂起锁，继续执行
        compiled_graph.invoke(None, config=config)

    # 如果完全没有记录，或者图已经跑完了 (next 为空且没有跑过的痕迹)
    elif not state_snapshot.values:
        sys_logger.info(
            f"首次启动 Reader02 测试流，跳过reader01？{root_running_config.run_reader02_test_skip_reader01_flag}.")
        initial_state = {
            "skip_reader01": root_running_config.run_reader02_test_skip_reader01_flag,
            "reader02_current_index": 0
        }

        # 第一次 Invoke：引擎会顺着 START -> start_router ，
        # 然后在 reader02_state_node 前，被 interrupt_before 物理挂起。
        compiled_graph.invoke(initial_state, config=config)

        sys_logger.info("引擎已在 Reader02 门前物理挂起 (模拟等待人工审核)。")
        sys_logger.info("收到继续指令，释放挂起锁，切入 Reader02 演进循环...")

        # 第二次 Invoke：大循环开始
        compiled_graph.invoke(None, config=config)

    else:
        sys_logger.info(
            "该 Thread ID 的图谱流转已彻底结束。如果还想要延续解析，则应调高/app/graph/app_graph_config.py/reader02_router_test_segment_num参数")


if __name__ == "__main__":
    run_reader02()
