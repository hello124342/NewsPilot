"""SummarizeNode: LLM 摘要总结节点

接收 raw_content、title、vendor，调用 LLM 生成 3 条核心要点。
支持 LLM 调用失败自动重试（3次指数退避）。
"""
import logging
import re
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from app.graph.state import PushState
from app.llm.provider import get_llm
from app.core.config import Settings

logger = logging.getLogger(__name__)

# 从 YAML 加载 Prompt 模板（失败时使用硬编码回退）
try:
    from app.prompts.loader import load_prompt
    SUMMARIZE_PROMPT = load_prompt("summarize")
except Exception:
    SUMMARIZE_PROMPT = """你是 AI 行业新闻分析师。请用中文总结以下新闻的 3 个核心要点，每条一行。

厂商：{vendor}
标题：{title}

正文：
{content}

请按以下格式回复（仅回复要点内容）：
1. 要点一
2. 要点二
3. 要点三"""


def parse_summary(text: str) -> list[str]:
    """解析 LLM 输出的总结文本为要点列表

    尝试按数字编号拆分，失败时按行分割。
    """
    # 尝试匹配 "1. xxx 2. xxx 3. xxx" 格式
    pattern = r"\d+\.\s*(.+?)(?=\n\d+\.|$)"
    matches = re.findall(pattern, text, re.DOTALL)
    if len(matches) >= 3:
        return [m.strip() for m in matches[:3]]

    # 降级：按行分割，去除常见列表标记
    lines = text.strip().split("\n")
    points = []
    for line in lines:
        line = line.strip()
        # 去除行首的列表标记（如 "- ", "1. ", "• " 等）
        if line and line[0] in "-•*":
            line = line[1:].strip()
        # 去除行首数字编号
        line = re.sub(r"^\d+\.\s*", "", line)
        if len(line) > 2:
            points.append(line)
    return points[:3]


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def _call_llm_summarize(llm, prompt: str) -> str:
    """调用 LLM 生成摘要（带重试）"""
    import time as _time
    start = _time.perf_counter()
    try:
        response = llm.invoke(prompt)
        elapsed = _time.perf_counter() - start
        _emit_llm_metric("summarize", elapsed, success=True)
        return response.content  # type: ignore[union-attr]
    except Exception:
        elapsed = _time.perf_counter() - start
        _emit_llm_metric("summarize", elapsed, success=False)
        raise


def _emit_llm_metric(operation: str, elapsed: float, success: bool) -> None:
    """发送 LLM 调用指标到 Prometheus（延迟导入避免循环依赖）"""
    try:
        from app.core.metrics import llm_call_duration_seconds, llm_call_errors_total
        llm_call_duration_seconds.labels(provider="auto", operation=operation).observe(elapsed)
        if not success:
            llm_call_errors_total.labels(
                provider="auto", operation=operation, error_type="LLMCallFailed"
            ).inc()
    except ImportError:
        pass


def summarize_node(state: PushState) -> PushState:
    """LLM 总结节点

    调用 LLM 对正文进行摘要提炼，生成 3 条核心要点。
    内容为空或 LLM 调用失败时设 status 为 FAILED。
    支持 3 次指数退避重试（1s, 2s, 4s）。
    """
    content = state.get("raw_content", "")
    if not content:
        logger.warning("summarize_node: empty content, skipping")
        state["status"] = "FAILED"
        return state

    try:
        settings = Settings()  # type: ignore[call-arg]
        llm = get_llm(settings)

        prompt = SUMMARIZE_PROMPT.format(
            vendor=state.get("vendor", "Unknown"),
            title=state.get("title", "Unknown"),
            content=content[:4000],  # 截断过长内容，控制 token 消耗
        )
        logger.info(f"Summarizing: {state.get('title', 'Unknown')[:50]}")

        result = _call_llm_summarize(llm, prompt)
        points = parse_summary(result)
        state["summary_points"] = points
        logger.info(f"Summary done: {len(points)} points generated")
    except Exception as e:
        logger.error(f"Summarize failed after retries: {e}")
        state["status"] = "FAILED"

    return state
