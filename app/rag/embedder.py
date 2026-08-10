"""Embedding 模型封装

调用 OpenAI text-embedding-3-small 生成文本向量。
复用项目现有的 Settings 配置和 tenacity 重试模式。
"""
import logging
import time as _time
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.core.config import Settings

logger = logging.getLogger(__name__)

# 每次 embed 的文本上限（token 估算：1 中文 ≈ 1.5 token，512 tokens ≈ 340 字）
_MAX_CHARS_PER_BATCH = 8000


def _get_openai_client():
    """获取 OpenAI 客户端（延迟导入避免启动时无网络报错）"""
    from openai import OpenAI
    settings = Settings()  # type: ignore[call-arg]
    return OpenAI(api_key=settings.OPENAI_API_KEY)


def _emit_embed_metric(duration: float, success: bool) -> None:
    """发送 embedding 调用指标到 Prometheus（延迟导入避免循环依赖）"""
    try:
        from app.core.metrics import rag_embed_duration_seconds, rag_embed_total
        rag_embed_duration_seconds.observe(duration)
        rag_embed_total.inc()
        if not success:
            from app.core.metrics import rag_embed_errors_total
            rag_embed_errors_total.inc()
    except ImportError:
        pass


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def _call_embedding_api(client, text: str) -> list[float]:
    """调用 OpenAI embedding API（带重试）"""
    start = _time.perf_counter()
    try:
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=text[:4096],  # OpenAI embedding 单次最大 8191 tokens，保守截断
            encoding_format="float",
        )
        elapsed = _time.perf_counter() - start
        _emit_embed_metric(elapsed, success=True)
        return response.data[0].embedding
    except Exception:
        elapsed = _time.perf_counter() - start
        _emit_embed_metric(elapsed, success=False)
        raise


def get_embedding(text: str) -> list[float]:
    """对单段文本生成 embedding 向量

    Args:
        text: 待嵌入的文本（超过 4096 字符自动截断）

    Returns:
        1536 维 float 向量（text-embedding-3-small 默认维度）
    """
    if not text or not text.strip():
        logger.warning("get_embedding: empty text, returning zero vector")
        return [0.0] * 1536

    client = _get_openai_client()
    return _call_embedding_api(client, text)


def build_article_embed_text(title: str, vendor: str, summary_points: str, channel: str = "") -> str:
    """构建文章的可检索文本（title + vendor + summary）

    将文章的关键信息拼接为一段文本，用于 embedding 和语义检索。
    不包含 raw_content 全文（原文在检索命中后回填给 LLM）。

    Args:
        title: 文章标题
        vendor: 来源厂商
        summary_points: LLM 摘要（换行分隔的要点）
        channel: 渠道类型（Blog/Twitter）

    Returns:
        用于 embedding 的文本
    """
    parts = [f"厂商: {vendor}", f"标题: {title}"]
    if channel:
        parts.append(f"来源: {channel}")
    if summary_points:
        parts.append(f"摘要: {summary_points}")
    return "\n".join(parts)
