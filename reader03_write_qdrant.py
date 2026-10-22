import sys
from dotenv import load_dotenv, find_dotenv
from app.database.qdrant_services import Reader03QdrantService
from app.utils.logger import sys_logger

load_dotenv(find_dotenv())


def run_qdrant_write():
    sys_logger.info("启动 Reader03 向量数据库写入器...")

    # 实例化 Service
    try:
        qdrant_service = Reader03QdrantService()
    except Exception as e:
        sys_logger.error(f"Qdrant 引擎启动失败，进程终止: {e}")
        sys.exit(1)

    try:
        # 兼顾 Embedding 接口并发率和 Qdrant 写入吞吐量
        batch_size = 50
        sys_logger.info(f"开始执行文本向量化，批次大小: {batch_size}")

        # ingest_chunks_ledger 内部已经封装了集合检查、索引创建、账本读取和批量 Upsert
        total_ingested = qdrant_service.ingest_chunks_ledger(batch_size=batch_size)

        if total_ingested > 0:
            sys_logger.info(f"入库结算： {total_ingested} 个向量切片。")
        else:
            sys_logger.warning("警告：入库数量为 0，请检查 reader03_semantic_chunks_ledger 是否有数据。")

    except Exception as e:
        sys_logger.error(f"向量入库异常: {e}")
    finally:

        qdrant_service.close()
        sys_logger.info("写入动作终了，安全释放 Qdrant 引擎。")


if __name__ == "__main__":
    run_qdrant_write()
