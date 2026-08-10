"""ChromaDB 向量存储

管理 ChromaDB collection 的 CRUD 操作。
使用 PersistentClient 本地持久化，数据目录为 ./chroma_data/。

设计原则：
- embedder.py 负责调用 OpenAI 生成向量
- vector_store.py 只负责 ChromaDB 读写，不关心向量来源
- 这种分离便于后续切换 embedding 模型或替换向量库
"""
import logging
import os
from typing import Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

logger = logging.getLogger(__name__)

# Collection 名称（固定，全局唯一）
COLLECTION_NAME = "news_articles"

# 数据目录（相对于项目根目录）
_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "chroma_data")

# 全局客户端（延迟初始化）
_client: Optional[chromadb.PersistentClient] = None
_collection: Optional[chromadb.Collection] = None


def _get_client() -> chromadb.PersistentClient:
    """获取或初始化 ChromaDB 持久化客户端"""
    global _client
    if _client is None:
        os.makedirs(_DATA_DIR, exist_ok=True)
        # PersistentClient 在 ChromaDB >= 0.5 中是标准持久化方式
        try:
            _client = chromadb.PersistentClient(
                path=_DATA_DIR,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        except TypeError:
            # ChromaDB < 1.0 可能不支持 settings 参数
            _client = chromadb.PersistentClient(path=_DATA_DIR)
        logger.info(f"ChromaDB client initialized, data dir: {_DATA_DIR}")
    return _client


def get_collection() -> chromadb.Collection:
    """获取或创建 'news_articles' collection

    Collection 使用默认 embedding function（由 embedder 预先计算向量后传入）。
    不绑定 ChromaDB 内置 embedding function，确保向量由我们控制。
    """
    global _collection
    if _collection is None:
        client = _get_client()
        try:
            _collection = client.get_collection(name=COLLECTION_NAME)
            logger.info(f"ChromaDB collection '{COLLECTION_NAME}' loaded ({_collection.count()} docs)")
        except Exception:
            _collection = client.create_collection(name=COLLECTION_NAME)
            logger.info(f"ChromaDB collection '{COLLECTION_NAME}' created")
    return _collection


def add_article(
    article_id: int,
    embedding: list[float],
    document: str,
    metadata: dict,
) -> str:
    """添加单篇文章到向量库

    Args:
        article_id: MySQL 中的 NewsArticle.id（用作 ChromaDB document id）
        embedding: 预计算的 embedding 向量
        document: 检索文本（用于 query 时返回，方便 debug）
        metadata: 元数据 {"vendor", "title", "url", "published_at", "channel"}

    Returns:
        ChromaDB document id（字符串形式的 article_id）

    Raises:
        ValueError: embedding 为空
    """
    if not embedding or all(v == 0.0 for v in embedding):
        raise ValueError(f"Cannot add article {article_id}: embedding is empty or zero vector")

    collection = get_collection()
    doc_id = str(article_id)

    # 幂等：已存在则先删后加（更新场景）
    try:
        existing = collection.get(ids=[doc_id])
        if existing and existing["ids"]:
            collection.delete(ids=[doc_id])
    except Exception:
        pass

    collection.add(
        ids=[doc_id],
        embeddings=[embedding],
        documents=[document[:4096]],  # 截断，ChromaDB 建议 document 不要过长
        metadatas=[metadata],
    )
    logger.debug(f"Article {article_id} added to ChromaDB: {metadata.get('title', '')[:50]}")
    return doc_id


def search(query_embedding: list[float], top_k: int = 5) -> list[dict]:
    """语义搜索

    Args:
        query_embedding: 用户问题的 embedding 向量
        top_k: 返回最相似的 N 篇文章

    Returns:
        [{article_id: int, document: str, metadata: dict, distance: float}, ...]
        按相似度降序排列

    Raises:
        RuntimeError: collection 为空
    """
    collection = get_collection()
    count = collection.count()
    if count == 0:
        logger.warning("ChromaDB collection is empty, returning no results")
        return []

    actual_k = min(top_k, count)
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=actual_k,
        include=["documents", "metadatas", "distances"],
    )

    articles = []
    if results["ids"] and results["ids"][0]:
        for i, doc_id in enumerate(results["ids"][0]):
            articles.append({
                "article_id": int(doc_id),
                "document": results["documents"][0][i] if results["documents"] else "",
                "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                "distance": results["distances"][0][i] if results["distances"] else 0.0,
            })

    logger.info(f"ChromaDB search: {len(articles)} results (top_k={top_k}, total={count})")
    return articles


def delete_article(article_id: int) -> None:
    """从向量库删除单篇文章"""
    collection = get_collection()
    try:
        collection.delete(ids=[str(article_id)])
    except Exception as e:
        logger.warning(f"Failed to delete article {article_id} from ChromaDB: {e}")


def collection_count() -> int:
    """返回 collection 中的文档总数"""
    try:
        return get_collection().count()
    except Exception:
        return 0


def reset_collection() -> None:
    """重置 collection（删除全部数据，用于测试）"""
    global _collection
    client = _get_client()
    try:
        client.delete_collection(name=COLLECTION_NAME)
    except Exception:
        pass
    _collection = None
    logger.info("ChromaDB collection reset")
