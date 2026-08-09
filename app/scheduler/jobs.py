"""APScheduler 定时任务定义

每天上午 9:00 执行 RSS 轮询、内容抓取和 LLM 总结推送。
"""
from app.main import process_rss_job


def schedule_rss_polling() -> None:
    """RSS 轮询调度入口

    由 APScheduler 在每天 9:00 自动触发。
    逐篇处理文章，单篇失败不影响其他。
    """
    try:
        result = process_rss_job()
        return result
    except Exception as e:
        return {"status": "error", "message": str(e)}
