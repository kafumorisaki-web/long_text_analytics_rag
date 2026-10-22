from neo4j import GraphDatabase
import os
from app.utils.logger import sys_logger


class Neo4jClient:
    def __init__(self):
        """初始化数据库驱动，建立连接池"""
        self.uri = os.getenv("NEO4J_URI")
        self.user = os.getenv("NEO4J_USER")
        self.password = os.getenv("NEO4J_PASSWORD")
        try:
            self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            # 验证连接
            self.driver.verify_connectivity()
            sys_logger.info("Neo4j 数据库连接池初始化成功。")
        except Exception as e:
            sys_logger.error(f"Neo4j 连接失败: {e}")
            raise

    def close(self):
        if self.driver is not None:
            self.driver.close()
            sys_logger.info("Neo4j 连接已关闭。")

    def execute_write(self, cypher_query: str, parameters: dict = None):
        """
        执行写事务（Create/Merge/Set/Delete）。将 Cypher 语句和参数交给底层的 transaction 引擎去跑，并返回执行结果。
        """
        parameters = parameters or {}

        def _write_tx(tx, query, params):
            result = tx.run(query, **params)
            return [record.data() for record in result]

        with self.driver.session() as session:
            try:

                records = session.execute_write(_write_tx, cypher_query, parameters)
                return records
            except Exception as e:
                sys_logger.error(f"写事务执行失败:\nCypher: {cypher_query}\nParams: {parameters}\nError: {e}")
                raise

    def execute_read(self, cypher_query: str, parameters: dict = None):
        """
        执行读事务（Match/Return）。
        """
        parameters = parameters or {}

        def _read_tx(tx, query, params):
            result = tx.run(query, **params)
            return [record.data() for record in result]

        with self.driver.session() as session:
            try:
                records = session.execute_read(_read_tx, cypher_query, parameters)
                return records
            except Exception as e:
                sys_logger.error(f"读事务执行失败:\nCypher: {cypher_query}\nParams: {parameters}\nError: {e}")
                raise
