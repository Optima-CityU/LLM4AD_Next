"""Internationalization (i18n) for the memory TUI.

Provides bilingual (Chinese/English) text for all UI elements.
"""

from __future__ import annotations

# Translation dictionary: key -> {zh, en}
TRANSLATIONS: dict[str, dict[str, str]] = {
    # Title / header
    "app_title": {"zh": "记忆管理", "en": "Memory Manager"},
    # Column headers
    "col_enabled": {"zh": "启用", "en": "Enabled"},
    "col_type": {"zh": "类型", "en": "Type"},
    "col_title": {"zh": "标题", "en": "Title"},
    "col_content": {"zh": "内容", "en": "Content"},
    "col_tags": {"zh": "标签", "en": "Tags"},
    # Enabled state
    "state_enabled": {"zh": "可注入", "en": "Injectable"},
    "state_disabled": {"zh": "已禁用", "en": "Disabled"},
    # Memory types (matches frontend memoryTypeLabel)
    "type_good_algorithm": {"zh": "优秀算法经验", "en": "Good Algorithm"},
    "type_error_reflection": {"zh": "错误反思", "en": "Error Reflection"},
    "type_domain_knowledge": {"zh": "领域知识", "en": "Domain Knowledge"},
    "type_general_insight": {"zh": "通用经验", "en": "General Insight"},
    # Status bar
    "status_showing": {"zh": "显示 {shown}/{total} 条记忆", "en": "Showing {shown}/{total} memories"},
    "status_page": {"zh": "第 {page} 页", "en": "Page {page}"},
    "status_scope": {"zh": "范围: {scope}", "en": "Scope: {scope}"},
    "status_search": {"zh": "搜索 '{query}': {count} 条结果", "en": "Search '{query}': {count} results"},
    "status_empty": {"zh": "没有记忆", "en": "No memories"},
    # Key bindings (footer)
    "key_quit": {"zh": "退出", "en": "Quit"},
    "key_refresh": {"zh": "刷新", "en": "Refresh"},
    "key_edit": {"zh": "编辑", "en": "Edit"},
    "key_toggle": {"zh": "启用/禁用", "en": "Toggle"},
    "key_delete": {"zh": "删除", "en": "Delete"},
    "key_search": {"zh": "搜索", "en": "Search"},
    "key_config": {"zh": "配置", "en": "Config"},
    "key_lang": {"zh": "语言", "en": "Language"},
    "key_save": {"zh": "保存", "en": "Save"},
    "key_cancel": {"zh": "取消", "en": "Cancel"},
    "key_close": {"zh": "关闭", "en": "Close"},
    # Edit dialog
    "edit_title": {"zh": "编辑记忆", "en": "Edit Memory"},
    "edit_field_title": {"zh": "标题", "en": "Title"},
    "edit_field_type": {"zh": "类型", "en": "Type"},
    "edit_field_content": {"zh": "内容", "en": "Content"},
    "edit_field_tags": {"zh": "标签 (逗号分隔)", "en": "Tags (comma-separated)"},
    "edit_field_enabled": {"zh": "启用 (参与检索注入)", "en": "Enabled (used in retrieval)"},
    # Search dialog
    "search_title": {"zh": "搜索记忆", "en": "Search Memories"},
    "search_placeholder": {"zh": "输入搜索关键词...", "en": "Enter search query..."},
    # Config dialog
    "config_title": {"zh": "配置与绑定", "en": "Configuration & Binding"},
    "config_connection": {"zh": "连接配置", "en": "Connection"},
    "config_base_url": {"zh": "服务地址", "en": "Base URL"},
    "config_jwt_secret": {"zh": "JWT 密钥", "en": "JWT Secret"},
    "config_binding": {"zh": "模型绑定", "en": "Provider Binding"},
    "config_chat": {"zh": "对话模型", "en": "Chat Model"},
    "config_embedding": {"zh": "向量模型", "en": "Embedding Model"},
    "config_status_ok": {"zh": "已配置", "en": "Configured"},
    "config_status_off": {"zh": "未配置", "en": "Not Configured"},
    "config_bind_btn": {"zh": "绑定模型", "en": "Bind Providers"},
    "config_embedding_locked": {
        "zh": "注意: 向量模型首次绑定后 model 和维度将锁定",
        "en": "Note: Embedding model & dimensions are locked after first binding",
    },
    "config_embedding_dim": {"zh": "向量维度", "en": "Dimensions"},
    "config_rerank": {"zh": "重排模型", "en": "Rerank Model"},
    "config_optional": {"zh": "(可选)", "en": "(optional)"},
    # Unconfigured guidance
    "guide_not_enabled": {"zh": "记忆功能未启用", "en": "Memory Not Enabled"},
    "guide_prompt": {
        "zh": "尚未配置记忆服务。按 c 打开配置，填写连接与模型信息后保存即可启用。",
        "en": (
            "Memory service is not configured. Press c to open config, "
            "fill in the settings, then save to enable."
        ),
    },
    "msg_config_saved": {"zh": "配置已保存，正在启动记忆...", "en": "Config saved, starting memory..."},
    # Loading
    "msg_loading": {"zh": "正在加载记忆...", "en": "Loading memories..."},
    # Insert / new memory
    "key_new": {"zh": "新增", "en": "New"},
    "new_title": {"zh": "新增记忆", "en": "New Memory"},
    "new_field_content": {"zh": "记忆内容 (将由模型提取为记忆卡片)", "en": "Content (extracted into memory cards)"},
    "new_field_language": {"zh": "语言 (auto/ZH/EN)", "en": "Language (auto/ZH/EN)"},
    "key_extract": {"zh": "提取", "en": "Extract"},
    "msg_extracting": {"zh": "正在提取记忆...", "en": "Extracting memory..."},
    "msg_extract_cancelled": {"zh": "已取消提取，本次未保存记忆", "en": "Extraction cancelled; no memory was saved"},
    "msg_extract_stream_ended": {
        "zh": "记忆服务的流式响应提前结束",
        "en": "MindMemOS stream ended before completion",
    },
    "msg_inserted": {"zh": "已启用 {count} 条记忆", "en": "Enabled {count} memory card(s)"},
    "msg_discarded": {"zh": "已丢弃 {count} 条记忆", "en": "Discarded {count} memory card(s)"},
    "msg_no_extract": {"zh": "未提取到可保存的记忆", "en": "No memory extracted"},
    "msg_content_empty": {"zh": "记忆内容不能为空", "en": "Content cannot be empty"},
    # Extraction preview (select which to keep)
    "preview_title": {"zh": "提取结果 — 勾选要保留的记忆", "en": "Extracted — check the ones to keep"},
    "preview_hint": {
        "zh": "空格勾选/取消 · 勾选=启用保留，未勾选=丢弃删除",
        "en": "space to toggle · checked = keep & enable, unchecked = discard",
    },
    "key_confirm": {"zh": "确认", "en": "Confirm"},
    "preview_empty": {"zh": "未提取到记忆卡片", "en": "No cards extracted"},
    "new_stage_draft": {"zh": "输入一段可复用的算法经验或反思", "en": "Describe a reusable algorithm insight or reflection"},
    "new_stage_progress": {"zh": "正在生成记忆预览", "en": "Generating memory preview"},
    "new_stage_preview": {"zh": "选择要保留的记忆", "en": "Choose memories to keep"},
    "key_cancel_extraction": {"zh": "取消提取", "en": "Cancel extraction"},
    # Detail
    "detail_title": {"zh": "记忆详情", "en": "Memory Detail"},
    "detail_id": {"zh": "记忆 ID", "en": "Memory ID"},
    "detail_status": {"zh": "状态", "en": "Status"},
    # Messages
    "msg_deleted": {"zh": "已删除记忆: {id}", "en": "Deleted memory: {id}"},
    "msg_enabled": {"zh": "已启用记忆: {id}", "en": "Enabled memory: {id}"},
    "msg_disabled": {"zh": "已禁用记忆: {id}", "en": "Disabled memory: {id}"},
    "msg_saved": {"zh": "已保存记忆", "en": "Memory saved"},
    "msg_bound": {"zh": "模型绑定成功", "en": "Providers bound successfully"},
    "msg_embed_model_locked": {
        "zh": "向量模型/维度已锁定，无法更改(仅可改 API Key / Base URL)，已恢复为锁定值。",
        "en": "Embedding model/dim is locked (only API Key / Base URL can change); reverted to locked value.",
    },
    "msg_error": {"zh": "错误: {error}", "en": "Error: {error}"},
    "msg_config_incomplete": {
        "zh": "配置不完整，请先在配置界面(c)绑定模型",
        "en": "Config incomplete, please bind providers in config (c)",
    },
}

# Memory type key -> translation key
MEMORY_TYPE_KEYS = {
    "good_algorithm": "type_good_algorithm",
    "error_reflection": "type_error_reflection",
    "domain_knowledge": "type_domain_knowledge",
    "general_insight": "type_general_insight",
}


class Translator:
    """Simple translator holding the current language."""

    def __init__(self, lang: str = "zh"):
        """Initialize with a language ('zh' or 'en')."""
        self.lang = lang if lang in ("zh", "en") else "zh"

    def toggle(self) -> None:
        """Switch between Chinese and English."""
        self.lang = "en" if self.lang == "zh" else "zh"

    def t(self, key: str, **kwargs: object) -> str:
        """Translate a key, with optional format arguments."""
        entry = TRANSLATIONS.get(key)
        if entry is None:
            return key
        text = entry.get(self.lang, entry.get("zh", key))
        if kwargs:
            try:
                return text.format(**kwargs)
            except (KeyError, IndexError):
                return text
        return text

    def memory_type(self, mem_type: str) -> str:
        """Get localized label for a memory type."""
        key = MEMORY_TYPE_KEYS.get(mem_type)
        if key:
            return self.t(key)
        return mem_type
