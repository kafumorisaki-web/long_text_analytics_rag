import json
import os
from typing import List, Dict, Any, Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models
from app.agents.llm_client import LLMClient
from app.database.qdrant_db_client import QdrantDBClient
from app.utils.logger import sys_logger
import app.database.app_database_config as app_database_config


class Reader03QdrantService:
    def __init__(self):
        self.db_conn = QdrantDBClient()
        self.client: QdrantClient = self.db_conn.get_client()
        self.llm_client = LLMClient()
        self.collection_name = app_database_config.init_collection_name
        self.vector_size = app_database_config.init_vector_size

    def close(self):
        self.db_conn.close()

    def ensure_collection_and_indexes(self):
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config={
                    "meta_vector": models.VectorParams(size=self.vector_size, distance=models.Distance.COSINE),
                    "text_vector": models.VectorParams(size=self.vector_size, distance=models.Distance.COSINE)
                }
            )
            self.client.create_payload_index(
                collection_name=self.collection_name,
                field_name="segment_id",
                field_schema=models.PayloadSchemaType.INTEGER
            )
            self.client.create_payload_index(
                collection_name=self.collection_name,
                field_name="entities",
                field_schema=models.PayloadSchemaType.KEYWORD
            )
            self.client.create_payload_index(
                collection_name=self.collection_name,
                field_name="type",
                field_schema=models.PayloadSchemaType.KEYWORD
            )
            sys_logger.info(f"成功初始化 Qdrant 集合: {self.collection_name} ")

    def _load_clean_segment_titles(self) -> Dict[int, str]:
        clean_ledger_path = app_database_config.load_clean_segment_titles_clean_ledger_path
        title_map = {}
        if os.path.exists(clean_ledger_path):
            try:
                with open(clean_ledger_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        seg_id = data.get("segment_num")
                        seg_name = data.get("segment_title", "").strip()
                        if seg_id is not None:
                            title_map[seg_id] = seg_name if seg_name else str(seg_id)
            except Exception as e:
                sys_logger.warning(f"读取 Clean Ledger 解析脉络标题失败: {e}")
        return title_map

    def ingest_chunks_ledger(self, batch_size: int = 50) -> int:
        ledger_path = app_database_config.ingest_chunks_ledger_reader03_chunking_node_ledger_path
        if not os.path.exists(ledger_path):
            sys_logger.error(f"切片账本不存在: {ledger_path}")
            return 0

        self.ensure_collection_and_indexes()
        title_map = self._load_clean_segment_titles()

        meta_components = app_database_config.ingest_chunks_ledger_meta_components
        custom_payload_fields = app_database_config.ingest_chunks_ledger_custom_payload_fields

        all_chunks = []
        with open(ledger_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    all_chunks.append(json.loads(line))

        if not all_chunks:
            sys_logger.warning("切片账本为空，取消入库。")
            return 0

        sys_logger.info(f"拉取到 {len(all_chunks)} 个切片记录")
        total_ingested = 0

        for i in range(0, len(all_chunks), batch_size):
            chunk_batch = all_chunks[i: i + batch_size]
            meta_texts = []
            raw_texts = []

            for c in chunk_batch:
                # 动态合成 meta_vector 文本特征
                meta_parts = []
                for comp_key in meta_components:
                    val = c.get(comp_key)
                    if isinstance(val, list):
                        meta_parts.append(f"{comp_key}: {' '.join(str(v) for v in val)}")
                    elif val:
                        meta_parts.append(f"{comp_key}: {val}")

                meta_texts.append("\n".join(meta_parts))
                raw_texts.append(c.get("chunk_text", ""))

            meta_vectors = self.llm_client.get_batch_embeddings(meta_texts)
            text_vectors = self.llm_client.get_batch_embeddings(raw_texts)

            points_to_upsert = []
            for chunk, vec_meta, vec_text in zip(chunk_batch, meta_vectors, text_vectors):

                seg_id = chunk["segment_num"]
                chk_idx = chunk["chunk_index"]
                point_id = seg_id * 10000 + chk_idx

                payload = {
                    "type": "原文",
                    "segment_id": seg_id,
                    "segment_name": title_map.get(seg_id, str(seg_id)),
                    "chunk_index": chk_idx,
                    "entities": chunk.get("entities", []),
                    "text": chunk.get("chunk_text", "")
                }

                for k in custom_payload_fields.keys():
                    if k in chunk:
                        payload[k] = chunk[k]

                points_to_upsert.append(models.PointStruct(
                    id=point_id,
                    vector={"meta_vector": vec_meta, "text_vector": vec_text},
                    payload=payload
                ))

            self.client.upsert(
                collection_name=self.collection_name,
                points=points_to_upsert,
                wait=True
            )
            total_ingested += len(points_to_upsert)
            sys_logger.info(f"批次入库完成: 进度 {total_ingested}/{len(all_chunks)}")

        sys_logger.info(f"向量化全量完成: 累计 {total_ingested} 条。")
        return total_ingested

    def search_relevant_chunks(
            self,
            query_text: str,
            segment_range: Optional[Dict[str, int]] = None,
            entities: Optional[List[str]] = None,
            type: Optional[str] = None,
            top_k: int = 5
    ) -> List[Dict[str, Any]]:

        query_vectors = self.llm_client.get_batch_embeddings([query_text])
        if not query_vectors:
            return []
        query_vec = query_vectors[0]

        must_conditions = []

        if segment_range:
            must_conditions.append(
                models.FieldCondition(
                    key="segment_id",
                    range=models.Range(**segment_range)
                )
            )

        if entities:
            must_conditions.append(
                models.FieldCondition(
                    key="entities",
                    match=models.MatchAny(any=entities)
                )
            )

        if type:
            must_conditions.append(
                models.FieldCondition(
                    key="type",
                    match=models.MatchValue(value=type)
                )
            )

        query_filter = models.Filter(must=must_conditions) if must_conditions else None

        search_results = self.client.query_points(
            collection_name=self.collection_name,
            prefetch=[
                models.Prefetch(query=query_vec, using="meta_vector", filter=query_filter, limit=top_k * 2),
                models.Prefetch(query=query_vec, using="text_vector", filter=query_filter, limit=top_k * 2),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=top_k
        )

        formatted_hits = []
        custom_payload_fields = app_database_config.ingest_chunks_ledger_custom_payload_fields

        for hit in search_results.points:

            hit_obj = {
                "point_id": hit.id,
                "score": round(hit.score, 4),
                "type": hit.payload.get("type"),
                "segment_id": hit.payload.get("segment_id"),
                "segment_name": hit.payload.get("segment_name"),
                "chunk_index": hit.payload.get("chunk_index"),
                "entities": hit.payload.get("entities", []),
                "text": hit.payload.get("text", "")
            }

            for k in custom_payload_fields.keys():
                hit_obj[k] = hit.payload.get(k)

            formatted_hits.append(hit_obj)

        return formatted_hits
