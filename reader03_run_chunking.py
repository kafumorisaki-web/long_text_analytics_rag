from dotenv import load_dotenv, find_dotenv
import root_running_config
from app.graph.workflow import compiled_graph
from app.utils.logger import sys_logger

load_dotenv(find_dotenv())


def run_reader03_test():


    config = root_running_config.run_reader03_test_thread_config


    state_snapshot = compiled_graph.get_state(config)


    if state_snapshot and state_snapshot.next:
        sys_logger.info(f"检测到引擎物理挂起在 {state_snapshot.next}，正在推进 Reader03...")

        compiled_graph.invoke(None, config=config)


    elif not state_snapshot.values:
        sys_logger.info(
            f"首次启动 Reader03 语义切片测试流 | "
            f"跳过 Reader01: {root_running_config.run_reader03_test_skip_reader01_flag}, "
            f"跳过 Reader02: {root_running_config.run_reader03_test_skip_reader02_flag}"
        )

        initial_state = {
            "skip_reader01": root_running_config.run_reader03_test_skip_reader01_flag,
            "skip_reader02": root_running_config.run_reader03_test_skip_reader02_flag,
            "reader03_current_index": 0
        }


        compiled_graph.invoke(initial_state, config=config)

        sys_logger.info("引擎已在 Reader03 前成功物理挂起。")
        sys_logger.info("自动下发继续指令，正式切入 Reader03 语义切片大循环...")


        compiled_graph.invoke(None, config=config)


    else:
        sys_logger.info(
            "该 Thread ID 的切片流转已彻底结束。如果还想要延续解析，请调高 app_graph_config.py 中的 reader03_router_test_segment_num 参数并开启续跑标志。")


if __name__ == "__main__":
    run_reader03_test()
