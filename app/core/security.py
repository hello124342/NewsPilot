"""飞书 Webhook 签名校验

实现飞书 Event 回调的 HMAC-SHA256 签名验证逻辑。
"""
import hashlib
import hmac


def verify_signature(
    timestamp: str,
    nonce: str,
    body: str,
    signature: str,
    app_secret: str,
) -> bool:
    """验证飞书 Webhook 回调签名

    飞书签名算法：将 timestamp + nonce + body 拼接后进行 HMAC-SHA256 哈希，
    与请求头中的签名对比。

    Args:
        timestamp: 请求头 X-Lark-Request-Timestamp
        nonce: 请求头 X-Lark-Request-Nonce
        body: 请求原始 JSON Body 字符串
        signature: 请求头 X-Lark-Signature 中的签名值
        app_secret: 飞书应用的 App Secret

    Returns:
        True 表示签名有效，False 表示无效
    """
    if not app_secret:
        # Secret 未配置：跳过验证（开发/测试模式）
        return True
    if not signature:
        return False

    raw = f"{timestamp}{nonce}{body}"
    expected = hmac.new(
        app_secret.encode("utf-8"),
        raw.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)
