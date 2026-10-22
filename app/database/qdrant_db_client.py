import os
from qdrant_client import QdrantClient
from app.utils.logger import sys_logger


class QdrantDBClient:

    def __init__(self):
        host = os.getenv("QDRANT_HOST")
        port = os.getenv("QDRANT_PORT")
        url = os.getenv("QDRANT_URL", None)
        api_key = os.getenv("QDRANT_API_KEY", None)

        try:
            if url:
                self.client = QdrantClient(url=url, api_key=api_key)
            else:
                self.client = QdrantClient(host=host, port=port, api_key=api_key, https=False)

            sys_logger.info(f"Qdrant SDK初始化成功 ({url or f'{host}:{port}'})。")
        except Exception as e:
            sys_logger.error(f"Qdrant 连接建立失败: {e}")
            raise

    def get_client(self) -> QdrantClient:
        return self.client

    def close(self):
        if hasattr(self, 'client') and self.client is not None:
            self.client.close()
            sys_logger.info("Qdrant 连接已安全关闭。")
