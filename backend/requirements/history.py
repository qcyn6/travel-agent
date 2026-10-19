"""Format the same conversation context for both requirement adapters."""


def format_conversation_history(
    conversation_history: list[dict[str, str]] | None,
) -> str:
    if not conversation_history:
        return "（暂无历史对话）"
    lines: list[str] = []
    for item in conversation_history:
        if not isinstance(item, dict):
            continue
        role = item.get("role", "unknown")
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        role_name = {"user": "用户", "assistant": "助手"}.get(role, role)
        lines.append(f"{role_name}：{content}")
    return "\n".join(lines) or "（暂无历史对话）"
