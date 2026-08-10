"""IntentNode: 用户查询意图识别节点

解析 @Bot 消息的 NL 文本，提取查询条件（厂商、时间范围）。
LLM 调用失败时降级为关键词匹配 + 默认参数。
支持 LLM 调用自动重试（3次指数退避）。
"""
import json
import logging
import re
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from app.graph.state import QueryState
from app.llm.provider import get_llm
from app.core.config import Settings

logger = logging.getLogger(__name__)

# 已知厂商列表（与 subscription/handler.py ALL_VENDORS 保持一致）
KNOWN_VENDORS = [
    "OpenAI", "Anthropic", "Google DeepMind", "DeepSeek",
    "Kimi (Moonshot)", "Z.ai / 智谱",
]

# 查询别名 → 标准名（扩展匹配，如"Google"→"Google DeepMind"）
_QUERY_VENDOR_ALIASES: dict[str, str] = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "claude": "Anthropic",
    "google": "Google DeepMind",
    "deepmind": "Google DeepMind",
    "google deepmind": "Google DeepMind",
    "deepseek": "DeepSeek",
    "deep seek": "DeepSeek",
    "kimi": "Kimi (Moonshot)",
    "moonshot": "Kimi (Moonshot)",
    "智谱": "Z.ai / 智谱",
    "z.ai": "Z.ai / 智谱",
}

# 从 YAML 加载 Prompt 模板（失败时使用硬编码回退）
try:
    from app.prompts.loader import load_prompt
    INTENT_PROMPT = load_prompt("intent")
except Exception:
    INTENT_PROMPT = """你是查询意图解析器。从用户消息中提取以下信息，返回 JSON。

用户消息：{query}

可用的 AI 厂商列表：{vendors}

返回格式（仅返回 JSON）：
{{"vendor": "厂商名或null", "days": 数字}}

days 含义：回溯的日历天数（含今天）
示例：
- "今天有哪些新闻" -> {{"vendor": null, "days": 1}}
- "昨天有哪些新闻" -> {{"vendor": null, "days": 1}}
- "最近3天 DeepSeek 的动态" -> {{"vendor": "DeepSeek", "days": 3}}
- "最近一周有什么新消息" -> {{"vendor": null, "days": 7}}
- "OpenAI 最近有什么新闻" -> {{"vendor": "OpenAI", "days": 7}}
- "有什么新消息" -> {{"vendor": null, "days": 3}}"""

# 日期关键词 → days 映射（用于 LLM 降级时的启发式提取）
_DATE_KEYWORD_MAP: list[tuple[str, int]] = [
    (r"今天", 1),
    (r"昨天|昨日", 1),
    (r"最近\s*(\d+)\s*天|近\s*(\d+)\s*天", 0),  # 动态捕获，value=0 表示从 regex group 提取
    (r"最近一周|近一周|本周|这周", 7),
    (r"最近一个月|近一个月|本月|这个月", 30),
    (r"最近两周|近两周", 14),
]


def _extract_days_from_query(query: str) -> int:
    """从查询文本中提取时间范围（用于 LLM 降级）

    支持：今天/昨天/最近N天/最近一周/最近一个月 等自然语言表达。
    未匹配到任何模式时返回默认 3 天。
    """
    for pattern, default_days in _DATE_KEYWORD_MAP:
        m = re.search(pattern, query)
        if m:
            if default_days == 0:
                # "最近N天" / "近N天" — 从 group 中提取数字
                num_str = m.group(1) or m.group(2)
                if num_str:
                    days = int(num_str)
                    return max(days, 1)  # 至少 1 天
            else:
                return default_days
    return 3  # 默认 3 天


def extract_vendor_from_query(query: str) -> str | None:
    """从查询文本中用别名匹配提取厂商名称（降级方案）"""
    query_lower = query.lower()
    # 先匹配别名（map "Google" → "Google DeepMind"）
    for alias, std_name in _QUERY_VENDOR_ALIASES.items():
        if alias in query_lower:
            return std_name
    # 再匹配标准全名
    for vendor in KNOWN_VENDORS:
        if vendor.lower() in query_lower:
            return vendor
    return None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def _call_llm_intent(llm, prompt: str) -> str:
    """调用 LLM 解析意图（带重试）"""
    import time as _time
    start = _time.perf_counter()
    try:
        response = llm.invoke(prompt)
        elapsed = _time.perf_counter() - start
        _emit_llm_metric("intent", elapsed, success=True)
        return response.content  # type: ignore[union-attr]
    except Exception:
        elapsed = _time.perf_counter() - start
        _emit_llm_metric("intent", elapsed, success=False)
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


def intent_node(state: QueryState) -> QueryState:
    """解析用户 @Bot 消息的查询意图

    优先用 LLM 解析（3次重试），失败时降级为关键词匹配。
    """
    query = state.get("user_query", "")
    logger.info(f"Parsing intent for: '{query[:80]}'")

    try:
        settings = Settings()  # type: ignore[call-arg]
        llm = get_llm(settings)

        prompt = INTENT_PROMPT.format(
            query=query,
            vendors=", ".join(KNOWN_VENDORS),
        )
        content = _call_llm_intent(llm, prompt)

        # 尝试提取 JSON
        json_match = re.search(r"\{[^}]+\}", content)
        if json_match:
            result = json.loads(json_match.group())
            days = result.get("days", 3)
            # 防御：days 必须是正整数
            if not isinstance(days, int) or days < 1:
                days = 3
            state["parsed_intent"] = {
                "vendor": result.get("vendor"),
                "days": days,
            }
            logger.info(f"Intent parsed: vendor={result.get('vendor')}, days={days}")
            return state

    except Exception as e:
        logger.warning(f"LLM intent parsing failed, falling back: {e}")

    # 降级：关键词匹配（含日期启发式提取）
    fallback_vendor = extract_vendor_from_query(query)
    fallback_days = _extract_days_from_query(query)
    state["parsed_intent"] = {
        "vendor": fallback_vendor,
        "days": fallback_days,
    }
    logger.info(f"Intent fallback: vendor={fallback_vendor}, days={fallback_days}")
    return state
