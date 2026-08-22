"""RAGAnswerNode: LLM 答案生成节点

将检索到的文章上下文 + 用户问题交给 LLM，综合生成带引用的答案。
"""
import logging
import time as _time
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from app.graph.state import QueryState
from app.core.resilience import CircuitBreakerOpenError

logger = logging.getLogger(__name__)


def _emit_degraded(path: str) -> None:
    """记录一次降级（LLM 不可用走文章列表兜底），延迟导入避免循环依赖"""
    try:
        from app.core.metrics import degraded_requests_total
        degraded_requests_total.labels(path=path).inc()
    except ImportError:
        pass

# 从 YAML 加载 Prompt 模板（失败时使用硬编码回退）
try:
    from app.prompts.loader import load_prompt
    RAG_ANSWER_PROMPT = load_prompt("rag_answer")
except Exception:
    RAG_ANSWER_PROMPT = """你是 AI 行业情报分析师。基于以下检索到的新闻文章，回答用户的问题。

## 规则
- 综合多篇文章信息，给出完整、准确的回答
- 每条关键信息后标注引用来源，格式：[来源 N]
- 如果检索到的文章信息不足以回答问题，诚实说明，不要编造
- 回答简洁有条理，控制在 500 字以内
- 结尾列出所有引用的来源

## 检索到的文章
{context}

## 用户问题
{question}

## 回答（Markdown 格式）"""


def _emit_answer_metric(duration: float, success: bool) -> None:
    """发送答案生成指标"""
    try:
        from app.core.metrics import rag_answer_duration_seconds, llm_call_errors_total
        rag_answer_duration_seconds.observe(duration)
        if not success:
            llm_call_errors_total.labels(
                provider="auto", operation="rag_answer", error_type="LLMCallFailed"
            ).inc()
    except ImportError:
        pass


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def _call_llm_answer(llm, prompt: str) -> str:
    """调用 LLM 生成 RAG 答案（带重试）"""
    start = _time.perf_counter()
    try:
        response = llm.invoke(prompt)
        elapsed = _time.perf_counter() - start
        _emit_answer_metric(elapsed, success=True)
        return response.content  # type: ignore[union-attr]
    except Exception:
        elapsed = _time.perf_counter() - start
        _emit_answer_metric(elapsed, success=False)
        raise


def _build_context_text(context: list[dict]) -> str:
    """将检索到的文章构建为 LLM 可读的上下文字符串"""
    if not context:
        return "（无相关文章）"

    parts = []
    for i, article in enumerate(context, 1):
        header = f"[来源 {i}] {article['title']} · {article['vendor']}"
        if article.get("published_at"):
            header += f" · {article['published_at']}"
        if article.get("channel"):
            header += f" · {article['channel']}"

        body = article.get("raw_content", "")
        if not body:
            # 如果没有原文，用摘要回退
            points = article.get("summary_points", [])
            if points and points[0]:
                body = "\n".join(p for p in points if p)

        parts.append(f"{header}\n{body}\n")
    return "\n---\n".join(parts)


def _extract_sources(context: list[dict]) -> list[dict]:
    """从 context 提取引用来源列表（供卡片按钮使用）"""
    sources = []
    for article in context:
        sources.append({
            "title": article.get("title", ""),
            "vendor": article.get("vendor", ""),
            "url": article.get("url", ""),
            "published_at": article.get("published_at", ""),
        })
    return sources


def rag_answer_node(state: QueryState) -> QueryState:
    """LLM 阅读检索上下文 + 用户问题 → 综合生成答案（带缓存）

    读取 rag_context（检索结果），构建 prompt，调用 LLM，
    将答案文本和引用来源写入 rag_answer。
    无检索结果时生成友好降级答复。
    """
    question = state.get("user_query", "")
    context = state.get("rag_context", [])

    if not question:
        state["rag_answer"] = {
            "answer_text": "你好！请问有什么关于 AI 行业的问题想了解？",
            "sources": [],
        }
        return state

    if not context:
        state["rag_answer"] = {
            "answer_text": (
                "😕 **目前没有找到直接相关的信息**\n\n"
                "可能的原因：\n"
                "- 知识库中还没有相关主题的文章\n"
                "- 可以尝试用更宽泛的关键词提问\n\n"
                "💡 你也可以发送「**OpenAI 最近有什么新闻**」查看最新的 AI 行业动态。"
            ),
            "sources": [],
        }
        logger.info(f"RAG answer: no context for '{question[:50]}'")
        return state

    # 缓存键：question + context 的 article_id 列表（确定性）
    import hashlib
    context_sig = ",".join(str(a.get("id", "")) for a in context)
    cache_key = f"rag_answer:{hashlib.sha256((question + context_sig).encode()).hexdigest()[:16]}"

    try:
        from app.llm.provider import get_llm, llm_circuit_breaker
        from app.core.config import Settings
        from app.core.multi_cache import get_llm_cache

        settings = Settings()  # type: ignore[call-arg]
        llm = get_llm(settings)

        context_text = _build_context_text(context)
        prompt = RAG_ANSWER_PROMPT.format(
            question=question,
            context=context_text,
        )

        logger.info(f"RAG answer: generating for '{question[:50]}' with {len(context)} sources")
        cache = get_llm_cache()
        # 熔断器包裹：LLM 故障时 OPEN 快速失败 → 降级为检索文章列表
        answer = cache.get_or_load(
            cache_key,
            lambda: llm_circuit_breaker.call(_call_llm_answer, llm, prompt),
        )

        state["rag_answer"] = {
            "answer_text": answer,
            "sources": _extract_sources(context),
        }
        logger.info(f"RAG answer generated: {len(answer)} chars, {len(context)} sources")

    except CircuitBreakerOpenError:
        # 降级：LLM 熔断，不生成答案，直接把检索到的文章列表返回给用户
        _emit_degraded("rag_answer")
        logger.warning("LLM circuit OPEN, RAG answer degraded to retrieved article list")
        state["rag_answer"] = _build_degraded_answer(context)

    except Exception as e:
        _emit_degraded("rag_answer")
        logger.error(f"RAG answer generation failed, degraded to article list: {e}")
        state["rag_answer"] = _build_degraded_answer(context)

    return state


def _build_degraded_answer(context: list[dict]) -> dict:
    """LLM 不可用时的兜底答复：不生成综合答案，直接列出检索到的相关文章。

    "AI 暂时不可用，以下是相关文章" —— 保证用户仍能拿到有用信息而非空/错误。
    """
    lines = ["🤖 **AI 生成服务暂时繁忙，以下是检索到的相关文章：**\n"]
    for i, article in enumerate(context, 1):
        title = article.get("title", "（无标题）")
        vendor = article.get("vendor", "")
        published = article.get("published_at", "")
        meta = " · ".join(x for x in (vendor, str(published)) if x)
        lines.append(f"{i}. **{title}**" + (f"\n   {meta}" if meta else ""))
    lines.append("\n💡 稍后再试可获得 AI 综合解读。")
    return {
        "answer_text": "\n".join(lines),
        "sources": _extract_sources(context),
    }
