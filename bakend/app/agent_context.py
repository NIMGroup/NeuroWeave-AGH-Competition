"""Small, retrievable tool observations without truncating current user intent."""
import json

from .assistant_runtime import preview
from .agent_query_pages import aligned_page, project_page


def prepared_plan_state(result):
    """Keep the review handshake distinct from a real plan validation failure."""
    if result.get("kind") != "data" or result.get("status") != "pending":
        return None
    from .assistant_proposal_review import REVIEW_BLOCK
    quality = result.get("quality") or {}
    review = quality.get("proposal_review") or {}
    blocks = [v for v in quality.get("blocking_issues", []) if v != REVIEW_BLOCK]
    script = result.get("phase") == "script" and not result.get("manifest_ready", True)
    version = result.get("script_plan_hash") if script else result.get("manifest_hash")
    state = {"id": result.get("id"), "version": version, "model_can_execute": False}
    if blocks or result.get("conflicts") or result.get("conflict_count") or result.get("preview_expired") or review.get("superseded_by") or review.get("decision") in {"revise", "clarify"}:
        return {**state, "stage": "needs_revision", "blocking_issues": blocks,
                "next_step": "读取当前方案的真实阻碍并修订；不要把待运行产物当已执行结果读取。"}
    complete = bool(version) and (script or bool(result.get("file_count")))
    if complete and review.get("decision") == "pending":
        return {**state, "stage": "needs_agent_review", "blocking_issues": [],
                "next_tool": {"name": "finish_task", "status": "awaiting_run", "artifact_ids": [result["id"]]},
                "next_step": "核对本版本脚本或变更与用户目标一致后调用 finish_task；后端绑定版本并完成待运行核验。该核验标记不要求重新扫描、生成方案或运行脚本；实际执行仍须用户点击运行并完成所需确认。"}
    if result.get("can_run_script") or result.get("can_confirm"):
        return {**state, "stage": "awaiting_run", "next_tool": {
                "name": "finish_task", "status": "awaiting_run", "artifact_ids": [result["id"]]}}
    return {**state, "stage": "incomplete"}


def tool_projection(result, reference=None, budget=6000):
    plan_state = prepared_plan_state(result)
    value = result
    content = result.get("content")
    if isinstance(content, str) and len(content) > budget // 2:
        try:
            structured = json.loads(content)
        except (ValueError, TypeError):
            structured = None
        if isinstance(structured, (dict, list)):
            data = structured if isinstance(structured, dict) else {"items": structured, "total_items": len(structured)}
            value = {k: v for k, v in result.items() if k != "content"}
            value.update(content_omitted=True, content_total_chars=len(content),
                         structured_preview=preview(data, max(1200, budget // 2)))
    limit = max(1200, budget - (1200 if plan_state else 600))
    page = project_page(value, limit)
    value = aligned_page(result, page if page is not None else preview(value, limit))
    if plan_state:
        value["prepared_plan_state"] = plan_state
    if reference:
        value["evidence_reference"] = reference
    if value.get("model_preview_truncated") or value.get("content_omitted"):
        value["preview_note"] = ("此处仅为查询结果摘要或样例，不能据此判断全量清单完整。"
            "完整结果按 evidence_reference 用 read_conversation_memory 分页读取，或按原路径/任务 ID 重新查询；历史证据不证明当前状态。"
            "本页 next_offset/next_cursor 与模型实际可见内容对齐；名称统计使用 coverage.next_group_offset，元数据使用 next_group_offset。"
            "有有效续页位置时继续读取下一页，不重读已见内容；若一条完整记录都未显示，缩小分页或查询范围。")
    return value
