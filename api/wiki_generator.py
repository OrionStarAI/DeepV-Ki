"""
Wiki 生成逻辑模块（原汁原味复刻旧系统）

将 wiki 生成的核心逻辑独立出来，避免在 task_queue.py 中处理复杂的字符串操作
完全复刻旧前端系统的生成算法和 Prompt
"""

import json
import re
import logging
import time
import xml.etree.ElementTree as ET
from typing import Dict, List, Any, Optional
from api.config import get_model_config
from api.prompts import (
    WIKI_STRUCTURE_COMPREHENSIVE_PROMPT,
    WIKI_STRUCTURE_CONCISE_PROMPT,
    WIKI_CONTENT_PROMPT
)
# from api.mermaid_adapter import render_mermaid_in_markdown  # 不再使用，Mermaid 渲染由前端处理
import adalflow as adal

logger = logging.getLogger(__name__)


# 语言映射表（全局）
LANGUAGE_MAP = {
    'en': 'English',
    'zh': 'Mandarin Chinese (中文)',
    'zh-tw': 'Traditional Chinese (繁體中文)',
    'ja': 'Japanese (日本語)',
    'ko': 'Korean (한국어)',
    'kr': 'Korean (한국어)',  # 兼容旧系统
    'es': 'Spanish (Español)',
    'fr': 'French (Français)',
    'pt-br': 'Brazilian Portuguese (Português Brasileiro)',
    'ru': 'Russian (Русский)',
    'vi': 'Vietnamese (Tiếng Việt)',
    'ar': 'Arabic (العربية)'
}


def clean_html_from_markdown(content: str) -> str:
    """
    从 markdown 内容中移除 HTML 标签

    移除如 <details>, <summary> 等 HTML 标签，只保留纯 markdown 格式

    Args:
        content: 原始内容（可能包含 HTML）

    Returns:
        清理后的纯 markdown 内容
    """
    # 移除 <details> 和 <summary> 块
    # 模式：<details>...<summary>...</summary>...\n</details>
    content = re.sub(
        r'<details>.*?<summary>.*?</summary>.*?\n</details>\n*',
        '',
        content,
        flags=re.DOTALL
    )

    # 如果还有其他 HTML 注释，也移除
    content = re.sub(r'<!--.*?-->', '', content, flags=re.DOTALL)

    # 修复可能的重复换行
    content = re.sub(r'\n{3,}', '\n\n', content)

    return content.strip()


def generate_wiki_structure(task: Dict[str, Any], documents_count: int, file_tree: str = '', readme: str = '') -> List[Dict[str, Any]]:
    """
    生成 wiki 结构（页面列表）

    完全复刻旧系统的逻辑：
    - 全面型: 8-12 页，带 sections
    - 简洁型: 4-6 页，扁平结构
    - 使用 XML 格式返回

    Args:
        task: 任务信息
        documents_count: 文档数量
        file_tree: 文件树（可选）
        readme: README 内容（可选）

    Returns:
        页面结构列表
    """
    task_id = task['task_id']
    repo_name = task['repo_name']
    owner = task.get('owner', '')
    language = task.get('language', 'zh')
    is_comprehensive = task.get('is_comprehensive', True)

    target_language = LANGUAGE_MAP.get(language, 'Mandarin Chinese (中文)')

    logger.info(f"[Task {task_id}] Generating structure - is_comprehensive: {is_comprehensive} (type: {type(is_comprehensive)})")

    # 构建完全复刻旧系统的 Prompt
    if is_comprehensive:
        logger.info(f"[Task {task_id}] Using COMPREHENSIVE structure prompt")
        # ========== 全面型 Prompt ==========
        structure_prompt = WIKI_STRUCTURE_COMPREHENSIVE_PROMPT.format(
            owner=owner,
            repo_name=repo_name,
            file_tree=file_tree if file_tree else '(File tree not available)',
            readme=readme if readme else '(README not available)',
            target_language=target_language
        )

    else:
        logger.info(f"[Task {task_id}] Using CONCISE structure prompt")
        # ========== 简洁型 Prompt ==========
        structure_prompt = WIKI_STRUCTURE_CONCISE_PROMPT.format(
            owner=owner,
            repo_name=repo_name,
            file_tree=file_tree if file_tree else '(File tree not available)',
            readme=readme if readme else '(README not available)',
            target_language=target_language
        )

    # ========== 新增：Token 检测 + 智能模型降级 ==========
    prompt_token_count = estimate_tokens(structure_prompt)
    provider = task['provider']
    model = task['model']

    logger.info(f"[Task {task_id}] 📊 Estimated prompt tokens: {prompt_token_count}")

    # 定义各模型的 token 限制（保留安全余量）
    model_token_limits = {
        'anthropic/claude-opus-4': 150000,      # 200K - 50K 余量
        'anthropic/claude-3.5-sonnet': 150000,  # 200K - 50K 余量
        'anthropic/claude-haiku-4.5': 150000,   # 200K - 50K 余量（容易超限的模型）
        'openai/gpt-4-turbo': 100000,           # 128K - 28K 余量
        'openai/gpt-4o': 100000,                # 128K - 28K 余量
        'qwen-plus': 120000,                    # DashScope Qwen-Plus
        'qwen-max': 120000,                     # DashScope Qwen-Max
        'qwen-turbo': 120000,                   # DashScope Qwen-Turbo
        'default': 150000
    }

    current_limit = model_token_limits.get(model, model_token_limits['default'])

    # 如果超过当前模型的限制，自动切换到 Gemini 2.5 Flash（支持 1M token）
    # 但如果只有 DashScope Key，不要切换到 OpenRouter
    from api.config import OPENROUTER_API_KEY

    if prompt_token_count > current_limit and OPENROUTER_API_KEY:
        logger.warning(
            f"[Task {task_id}] ⚠️ Prompt too large for {model}: "
            f"{prompt_token_count} tokens > {current_limit} limit. "
            f"Auto-switching to google/gemini-2.5-flash"
        )
        provider = 'openrouter'  # Google Gemini 通过 OpenRouter 使用
        model = 'google/gemini-2.5-flash'  # 1M token 上限
    elif prompt_token_count > current_limit:
        logger.warning(
            f"[Task {task_id}] ⚠️ Prompt too large for {model}: {prompt_token_count} > {current_limit}. "
            f"Cannot switch to Gemini (no OpenRouter key). Continuing with current model."
        )

    # 调用 AI 生成
    generator_config = get_model_config(provider, model)
    generator = adal.Generator(
        model_client=generator_config["model_client"](),
        model_kwargs=generator_config["model_kwargs"]
    )

    # 如果强制刷新，添加随机噪声以绕过缓存
    if task.get('force_refresh'):
        structure_prompt += f"\n\n<!-- Cache Buster: {time.time()} -->"
        logger.info(f"[Task {task_id}] 🔄 Force refresh enabled: Added cache buster to structure prompt")

    logger.info(f"[Task {task_id}] Calling AI to generate structure (comprehensive={is_comprehensive}, model={model})...")
    structure_response = generator(prompt_kwargs={"input_str": structure_prompt})
    response_text = str(structure_response.data if hasattr(structure_response, 'data') else structure_response)

    # Log usage information if available and track cost
    try:
        if hasattr(structure_response, 'raw_response') and structure_response.raw_response:
            raw_resp = structure_response.raw_response
            if hasattr(raw_resp, 'usage') and raw_resp.usage:
                usage = raw_resp.usage
                prompt_tokens = getattr(usage, 'prompt_tokens', None)
                completion_tokens = getattr(usage, 'completion_tokens', None)
                total_tokens = getattr(usage, 'total_tokens', None)
                cost = getattr(usage, 'cost', None)

                log_parts = []
                if prompt_tokens is not None:
                    log_parts.append(f"prompt_tokens: {prompt_tokens}")
                if completion_tokens is not None:
                    log_parts.append(f"completion_tokens: {completion_tokens}")
                if total_tokens is not None:
                    log_parts.append(f"total_tokens: {total_tokens}")
                if cost is not None:
                    log_parts.append(f"cost: ${cost:.5f}")

                if log_parts:
                    logger.info(f"[Task {task_id}] Wiki structure generation - {', '.join(log_parts)}")

                # Track cost
                try:
                    from api.cost_tracker import get_cost_tracker
                    cost_tracker = get_cost_tracker(task_id)
                    # Even if cost is None (0), we should track tokens
                    cost_tracker.add_llm_cost(
                        prompt_tokens=prompt_tokens or 0,
                        completion_tokens=completion_tokens or 0,
                        total_tokens=total_tokens or 0,
                        cost=cost or 0.0
                    )
                except Exception as e:
                    logger.debug(f"[Task {task_id}] Could not track LLM cost: {e}")
    except Exception as e:
        logger.debug(f"[Task {task_id}] Could not extract usage info: {e}")

    logger.debug(f"[Task {task_id}] Raw structure response: {response_text[:500]}...")

    # 解析 XML
    try:
        pages_structure = parse_wiki_structure_xml(response_text, task_id, is_comprehensive)
        logger.info(f"[Task {task_id}] ✓ Parsed {len(pages_structure)} pages from structure")
        return pages_structure
    except Exception as e:
        logger.warning(f"[Task {task_id}] ✗ Failed to parse structure XML: {e}")
        logger.debug(f"[Task {task_id}] Failed XML text: {response_text[:1000]}")

        # 回退到默认结构
        return get_default_structure(is_comprehensive, target_language)


def parse_wiki_structure_xml(xml_text: str, task_id: str, is_comprehensive: bool) -> List[Dict[str, Any]]:
    """
    解析 XML 格式的 wiki 结构

    完全复刻旧系统的 XML 解析逻辑
    """
    # 清理 XML 文本
    xml_text = xml_text.strip()

    # 移除可能的 markdown 代码块
    if '```' in xml_text:
        xml_match = re.search(r'```(?:xml)?\s*(<wiki_structure>.*?</wiki_structure>)\s*```', xml_text, re.DOTALL)
        if xml_match:
            xml_text = xml_match.group(1)

    # 提取 <wiki_structure> 内容
    if '<wiki_structure>' in xml_text and '</wiki_structure>' in xml_text:
        start = xml_text.find('<wiki_structure>')
        end = xml_text.find('</wiki_structure>') + len('</wiki_structure>')
        xml_text = xml_text[start:end]

    # 解析 XML
    root = ET.fromstring(xml_text)

    # 提取基本信息
    title = root.find('title')
    description = root.find('description')

    wiki_title = title.text if title is not None else 'Wiki'
    wiki_description = description.text if description is not None else 'Project documentation'

    # 解析 pages
    pages = []
    pages_element = root.find('pages')

    if pages_element is not None:
        for page_elem in pages_element.findall('page'):
            page_id = page_elem.get('id', f'page-{len(pages) + 1}')

            page_data = {
                'id': page_id,
                'title': page_elem.find('title').text if page_elem.find('title') is not None else 'Untitled',
                'description': page_elem.find('description').text if page_elem.find('description') is not None else '',
                'importance': page_elem.find('importance').text if page_elem.find('importance') is not None else 'medium',
            }

            # 提取 relevant_files
            relevant_files_elem = page_elem.find('relevant_files')
            if relevant_files_elem is not None:
                file_paths = [fp.text for fp in relevant_files_elem.findall('file_path') if fp.text]
                page_data['filePaths'] = file_paths
            else:
                page_data['filePaths'] = []

            # 提取 related_pages
            related_pages_elem = page_elem.find('related_pages')
            if related_pages_elem is not None:
                related = [rp.text for rp in related_pages_elem.findall('related') if rp.text]
                page_data['relatedPages'] = related
            else:
                page_data['relatedPages'] = []

            # 提取 parent_section
            parent_section_elem = page_elem.find('parent_section')
            if parent_section_elem is not None:
                page_data['parentSection'] = parent_section_elem.text

            pages.append(page_data)

    # 解析 sections（仅全面型）
    sections = []
    if is_comprehensive:
        sections_element = root.find('sections')
        if sections_element is not None:
            for section_elem in sections_element.findall('section'):
                section_data = {
                    'id': section_elem.get('id', f'section-{len(sections) + 1}'),
                    'title': section_elem.find('title').text if section_elem.find('title') is not None else 'Untitled Section',
                }

                # 提取该分区的页面
                pages_elem = section_elem.find('pages')
                if pages_elem is not None:
                    section_data['pages'] = [pr.text for pr in pages_elem.findall('page_ref') if pr.text]
                else:
                    section_data['pages'] = []

                sections.append(section_data)

    logger.info(f"[Task {task_id}] Parsed {len(pages)} pages, {len(sections)} sections")

    # 验证页面数量并强制执行约束
    if is_comprehensive:
        max_pages = 12
        min_pages = 8
        if len(pages) > max_pages:
            logger.warning(f"[Task {task_id}] ⚠️ Model generated {len(pages)} pages, exceeding limit of {max_pages}. Truncating to {max_pages} pages.")
            pages = pages[:max_pages]
        elif len(pages) < min_pages:
            logger.warning(f"[Task {task_id}] ⚠️ Model generated only {len(pages)} pages, below minimum of {min_pages}.")
    else:
        max_pages = 6
        min_pages = 4
        if len(pages) > max_pages:
            logger.warning(f"[Task {task_id}] ⚠️ Model generated {len(pages)} pages, exceeding limit of {max_pages}. Truncating to {max_pages} pages.")
            pages = pages[:max_pages]
        elif len(pages) < min_pages:
            logger.warning(f"[Task {task_id}] ⚠️ Model generated only {len(pages)} pages, below minimum of {min_pages}.")

    return pages


def get_default_structure(is_comprehensive: bool, language: str) -> List[Dict[str, Any]]:
    """
    获取默认的 wiki 结构（当解析失败时使用）
    """
    if is_comprehensive:
        return [
            {"id": "overview", "title": "概述" if 'Chinese' in language else "Overview", "importance": "high", "description": "项目概述", "filePaths": []},
            {"id": "architecture", "title": "架构" if 'Chinese' in language else "Architecture", "importance": "high", "description": "系统架构", "filePaths": []},
            {"id": "features", "title": "核心功能" if 'Chinese' in language else "Core Features", "importance": "high", "description": "主要功能", "filePaths": []},
            {"id": "setup", "title": "安装指南" if 'Chinese' in language else "Setup Guide", "importance": "medium", "description": "如何安装", "filePaths": []},
            {"id": "api", "title": "API 参考" if 'Chinese' in language else "API Reference", "importance": "medium", "description": "API 文档", "filePaths": []},
        ]
    else:
        return [
            {"id": "overview", "title": "概述" if 'Chinese' in language else "Overview", "importance": "high", "description": "项目概述", "filePaths": []},
            {"id": "getting-started", "title": "快速开始" if 'Chinese' in language else "Getting Started", "importance": "high", "description": "快速上手", "filePaths": []},
            {"id": "usage", "title": "使用示例" if 'Chinese' in language else "Usage Examples", "importance": "medium", "description": "使用方法", "filePaths": []},
        ]


def estimate_tokens(text: str) -> int:
    """
    粗略估算文本的 token 数量（用于快速检测）
    基于经验法则：平均 1 个 token ≈ 4 个字符

    Args:
        text: 输入文本

    Returns:
        估算的 token 数
    """
    return len(text) // 4


def generate_page_content(task: Dict[str, Any], page_info: Dict[str, Any], relevant_code: str = "", file_paths: List[str] = None) -> str:
    """
    生成单个页面的内容

    完全复刻旧系统的页面生成 Prompt

    Args:
        task: 任务信息
        page_info: 页面信息（id, title, description, filePaths）
        relevant_code: RAG检索到的相关代码
        file_paths: 从 RAG 检索得到的实际文件路径列表

    Returns:
        Markdown 内容（不包含 HTML 标签）
    """
    task_id = task['task_id']
    page_title = page_info.get('title', 'Untitled')
    description = page_info.get('description', 'Technical documentation')

    # 优先使用传入的 file_paths，否则使用 page_info 中的
    if file_paths is None:
        file_paths = page_info.get('filePaths', [])

    language = task.get('language', 'zh')

    target_language = LANGUAGE_MAP.get(language, 'Mandarin Chinese (中文)')

    # 生成文件路径列表（markdown 格式）
    file_paths_md = '\n'.join([f"- {fp}" for fp in file_paths]) if file_paths else '(No specific files provided)'

    # 添加实际代码内容
    code_context = ""
    if relevant_code:
        code_context = f"""

RELEVANT SOURCE CODE FROM THE PROJECT:
======================================
{relevant_code[:15000]}
======================================

Use the above source code as the basis for generating the wiki content.
"""
    else:
        code_context = "\n\nWARNING: No source code was provided. This should not happen. The wiki page must be based on actual source code."

    # ========== 完全复刻旧系统的页面内容 Prompt ==========
    content_prompt = WIKI_CONTENT_PROMPT.replace(
        "__PAGE_TITLE__", page_title
    ).replace(
        "__CODE_CONTEXT__", code_context
    ).replace(
        "__FILE_PATHS_MD__", file_paths_md
    ).replace(
        "__TARGET_LANGUAGE__", target_language
    )

    # ========== 新增：Token 检测 + 智能模型降级 ==========
    prompt_token_count = estimate_tokens(content_prompt)
    provider = task['provider']
    model = task['model']

    logger.info(f"[Task {task_id}] 📊 Estimated prompt tokens: {prompt_token_count}")

    # 定义各模型的 token 限制（保留安全余量）
    model_token_limits = {
        'anthropic/claude-opus-4': 150000,      # 200K - 50K 余量
        'anthropic/claude-3.5-sonnet': 150000,  # 200K - 50K 余量
        'anthropic/claude-haiku-4.5': 150000,   # 200K - 50K 余量（容易超限的模型）
        'openai/gpt-4-turbo': 100000,           # 128K - 28K 余量
        'openai/gpt-4o': 100000,                # 128K - 28K 余量
        'qwen-plus': 120000,                    # DashScope Qwen-Plus
        'qwen-max': 120000,                     # DashScope Qwen-Max
        'qwen-turbo': 120000,                   # DashScope Qwen-Turbo
        'default': 150000
    }

    current_limit = model_token_limits.get(model, model_token_limits['default'])

    # 如果超过当前模型的限制，自动切换到 Gemini 2.5 Flash（支持 1M token）
    # 但如果只有 DashScope Key，不要切换到 OpenRouter
    from api.config import OPENROUTER_API_KEY

    if prompt_token_count > current_limit and OPENROUTER_API_KEY:
        logger.warning(
            f"[Task {task_id}] ⚠️ Prompt too large for {model}: "
            f"{prompt_token_count} tokens > {current_limit} limit. "
            f"Auto-switching to google/gemini-2.5-flash"
        )
        provider = 'openrouter'  # Google Gemini 通过 OpenRouter 使用
        model = 'google/gemini-2.5-flash'  # 1M token 上限
    elif prompt_token_count > current_limit:
        logger.warning(
            f"[Task {task_id}] ⚠️ Prompt too large for {model}: {prompt_token_count} > {current_limit}. "
            f"Cannot switch to Gemini (no OpenRouter key). Continuing with current model."
        )

    generator_config = get_model_config(provider, model)
    generator = adal.Generator(
        model_client=generator_config["model_client"](),
        model_kwargs=generator_config["model_kwargs"]
    )

    # 如果强制刷新，添加随机噪声以绕过缓存
    if task.get('force_refresh'):
        content_prompt += f"\n\n<!-- Cache Buster: {time.time()} -->"
        logger.info(f"[Task {task_id}] 🔄 Force refresh enabled: Added cache buster to content prompt for page '{page_title}'")

    logger.info(f"[Task {task_id}] Generating content for page: {page_title}")
    content_response = generator(prompt_kwargs={"input_str": content_prompt})
    content = str(content_response.data if hasattr(content_response, 'data') else content_response)

    # Log usage information if available and track cost
    try:
        # Check if usage is directly available on content_response (GeneratorOutput)
        usage = getattr(content_response, 'usage', None)

        # Fallback: check in raw_response if usage is not directly available
        if not usage and hasattr(content_response, 'raw_response'):
            raw_resp = content_response.raw_response
            if hasattr(raw_resp, 'usage'):
                usage = raw_resp.usage

        # Debug log for content_response structure
        if not usage:
            logger.debug(f"[Task {task_id}] No usage found. content_response type: {type(content_response)}")
            logger.debug(f"[Task {task_id}] content_response dir: {dir(content_response)}")
            if hasattr(content_response, 'raw_response'):
                logger.debug(f"[Task {task_id}] raw_response type: {type(content_response.raw_response)}")
                logger.debug(f"[Task {task_id}] raw_response dir: {dir(content_response.raw_response)}")

        if usage:
            prompt_tokens = getattr(usage, 'prompt_tokens', None)
            completion_tokens = getattr(usage, 'completion_tokens', None)
            total_tokens = getattr(usage, 'total_tokens', None)
            cost = getattr(usage, 'cost', None)

            log_parts = []
            if prompt_tokens is not None:
                log_parts.append(f"prompt_tokens: {prompt_tokens}")
            if completion_tokens is not None:
                log_parts.append(f"completion_tokens: {completion_tokens}")
            if total_tokens is not None:
                log_parts.append(f"total_tokens: {total_tokens}")
            if cost is not None:
                log_parts.append(f"cost: ${cost:.5f}")

            if log_parts:
                logger.info(f"[Task {task_id}] Page '{page_title}' content generation - {', '.join(log_parts)}")

            # Track cost
            try:
                from api.cost_tracker import get_cost_tracker
                cost_tracker = get_cost_tracker(task_id)
                # Even if cost is None (0), we should track tokens
                cost_tracker.add_llm_cost(
                    prompt_tokens=prompt_tokens or 0,
                    completion_tokens=completion_tokens or 0,
                    total_tokens=total_tokens or 0,
                    cost=cost or 0.0
                )
            except Exception as e:
                logger.debug(f"[Task {task_id}] Could not track LLM cost for page '{page_title}': {e}")
    except Exception as e:
        logger.debug(f"[Task {task_id}] Could not extract usage info for page '{page_title}': {e}")

    logger.info(f"[Task {task_id}] ✓ Generated {len(content)} chars for page: {page_title}")

    # 清理 HTML 标签（防止 <details> 等 HTML 混入 markdown）
    content = clean_html_from_markdown(content)

    return content


def generate_wiki(task: Dict[str, Any], rag: Any, progress_callback=None) -> tuple[Dict[str, Any], int]:
    """
    生成完整的 Wiki（结构 + 所有页面内容）

    完全复刻旧系统的生成流程

    Args:
        task: 任务信息
        rag: RAG 实例（包含文档数据）
        progress_callback: 可选的进度回调函数 (progress_pct, current_stage, detail_message)

    Returns:
        tuple: (Wiki 结构, 文档数量)
    """
    task_id = task['task_id']
    repo_name = task['repo_name']

    logger.info(f"[Task {task_id}] Starting wiki generation for: {repo_name}")

    # 获取文档数量
    try:
        documents_count = len(rag.documents) if hasattr(rag, 'documents') and rag.documents else 0
    except:
        documents_count = 0

    logger.info(f"[Task {task_id}] Repository has {documents_count} documents")

    # 构建文件树和提取 README
    file_tree = ""
    readme = ""

    # logger.info(f"[Task {task_id}] 🔍 Checking RAG documents...")
    # logger.info(f"[Task {task_id}] - hasattr(rag, 'documents'): {hasattr(rag, 'documents')}")

    if hasattr(rag, 'documents'):
        # logger.info(f"[Task {task_id}] - rag.documents is None: {rag.documents is None}")
        if rag.documents:
            # logger.info(f"[Task {task_id}] - len(rag.documents): {len(rag.documents)}")
            # logger.info(f"[Task {task_id}] - type(rag.documents[0]): {type(rag.documents[0]) if rag.documents else 'N/A'}")

            # 检查第一个文档的结构
            if rag.documents:
                first_doc = rag.documents[0]
                # logger.info(f"[Task {task_id}] - First doc attributes: {dir(first_doc)}")
                # logger.info(f"[Task {task_id}] - hasattr(first_doc, 'meta_data'): {hasattr(first_doc, 'meta_data')}")
                # logger.info(f"[Task {task_id}] - hasattr(first_doc, 'text'): {hasattr(first_doc, 'text')}")

                if hasattr(first_doc, 'meta_data'):
                    pass
                    # logger.info(f"[Task {task_id}] - first_doc.meta_data: {first_doc.meta_data}")

    if hasattr(rag, 'documents') and rag.documents:
        # 生成文件树
        file_paths = []
        for i, doc in enumerate(rag.documents):
            if hasattr(doc, 'meta_data') and doc.meta_data:
                file_path = doc.meta_data.get('file_path', '')
                if file_path:
                    file_paths.append(file_path)
                    if i < 3:  # 只打印前3个
                        pass
                        # logger.info(f"[Task {task_id}] - Doc {i} file_path: {file_path}")

        file_tree = "\n".join(sorted(file_paths))
        logger.info(f"[Task {task_id}] ✅ Generated file tree with {len(file_paths)} files")

        if len(file_paths) == 0:
            logger.error(f"[Task {task_id}] ❌ WARNING: No file paths extracted from {len(rag.documents)} documents!")

        # 提取 README 内容
        for doc in rag.documents:
            if hasattr(doc, 'meta_data') and doc.meta_data:
                file_path = doc.meta_data.get('file_path', '').lower()
                if 'readme' in file_path:
                    readme = str(doc.text) if hasattr(doc, 'text') else str(doc)
                    logger.info(f"[Task {task_id}] ✅ Found README file: {file_path}")
                    logger.info(f"[Task {task_id}] - README length: {len(readme)} chars")
                    break
    else:
        logger.error(f"[Task {task_id}] ❌ CRITICAL: RAG has no documents!")

    # Step 1: 生成 Wiki 结构
    logger.info(f"[Task {task_id}] Step 1: Generating wiki structure...")
    pages_structure = generate_wiki_structure(task, documents_count, file_tree, readme)

    # 调用进度回调（结构生成完成）
    if progress_callback:
        progress_callback(70, 'structure', f'Wiki structure generated with {len(pages_structure)} pages')

    # Step 2: 为每个页面生成内容
    logger.info(f"[Task {task_id}] Step 2: Generating content for {len(pages_structure)} pages...")

    for i, page in enumerate(pages_structure):
        page_title = page['title']
        logger.info(f"[Task {task_id}] Generating page {i+1}/{len(pages_structure)}: {page_title}")

        # 调用进度回调（如果提供）
        if progress_callback:
            progress_pct = 70 + int((25 * i) / len(pages_structure))
            detail_msg = f'Generating page {i+1}/{len(pages_structure)}: {page_title}'
            progress_callback(progress_pct, 'pages', detail_msg)

        try:
            # 使用 RAG retriever 检索相关代码和文件路径
            relevant_code = ""
            file_paths = []

            # logger.info(f"[Task {task_id}] 🔍 Starting code retrieval for page: {page['title']}")
            # logger.info(f"[Task {task_id}] - hasattr(rag, 'retriever'): {hasattr(rag, 'retriever')}")

            if hasattr(rag, 'retriever'):
                # logger.info(f"[Task {task_id}] - rag.retriever is None: {rag.retriever is None}")
                # logger.info(f"[Task {task_id}] - type(rag.retriever): {type(rag.retriever)}")
                pass

            if hasattr(rag, 'retriever') and rag.retriever:
                try:
                    # 根据页面标题和描述检索相关代码
                    query = f"{page.get('title', '')} {page.get('description', '')}"
                    # logger.info(f"[Task {task_id}] 📋 Retrieval query: {query}")

                    # 使用 retriever 的 call 方法
                    # logger.info(f"[Task {task_id}] 🚀 Calling rag.retriever.call()")
                    retrieved_docs = rag.retriever.call(input=query, top_k=10)

                    # logger.info(f"[Task {task_id}] - Retrieved docs type: {type(retrieved_docs)}")
                    # logger.info(f"[Task {task_id}] - Retrieved docs count: {len(retrieved_docs) if retrieved_docs else 0}")

                    if retrieved_docs and len(retrieved_docs) > 0:
                        # 检查第一个返回文档的结构（RetrieverOutput）
                        first_output = retrieved_docs[0]
                        # logger.info(f"[Task {task_id}] - First output type: {type(first_output)}")
                        # logger.info(f"[Task {task_id}] - First output full object: {first_output}")
                        # logger.info(f"[Task {task_id}] - hasattr 'documents': {hasattr(first_output, 'documents')}")
                        # logger.info(f"[Task {task_id}] - hasattr 'doc_indices': {hasattr(first_output, 'doc_indices')}")
                        # logger.info(f"[Task {task_id}] - hasattr 'doc_scores': {hasattr(first_output, 'doc_scores')}")

                        code_snippets = []

                        # RetrieverOutput 使用 doc_indices 指向 rag.documents
                        for idx_out, retriever_output in enumerate(retrieved_docs):
                            # logger.info(f"[Task {task_id}] - Processing RetrieverOutput #{idx_out}")

                            # 使用 doc_indices 从 rag.documents 提取实际文档
                            if hasattr(retriever_output, 'doc_indices') and retriever_output.doc_indices:
                                doc_indices = retriever_output.doc_indices
                                # logger.info(f"[Task {task_id}] ✅ Found {len(doc_indices)} doc_indices")

                                # 从 rag.documents 中提取
                                if hasattr(rag, 'documents') and rag.documents:
                                    for doc_idx in doc_indices:
                                        if 0 <= doc_idx < len(rag.documents):
                                            doc = rag.documents[doc_idx]

                                            # 提取文本内容
                                            doc_text = None
                                            if hasattr(doc, 'text'):
                                                doc_text = str(doc.text)
                                            elif isinstance(doc, dict) and 'text' in doc:
                                                doc_text = str(doc['text'])
                                            elif isinstance(doc, str):
                                                doc_text = doc

                                            if doc_text:
                                                code_snippets.append(doc_text)
                                                if len(code_snippets) == 1:
                                                    pass
                                                    # logger.info(f"[Task {task_id}] - First doc text length: {len(doc_text)} chars")

                                            # 提取文件路径
                                            file_path = None
                                            if hasattr(doc, 'meta_data') and doc.meta_data:
                                                file_path = doc.meta_data.get('file_path', '')
                                                if len(file_paths) == 0:
                                                    pass
                                                    # logger.info(f"[Task {task_id}] - First doc meta_data: {doc.meta_data}")
                                            elif isinstance(doc, dict) and 'meta_data' in doc:
                                                file_path = doc['meta_data'].get('file_path', '')

                                            if file_path and file_path not in file_paths:
                                                file_paths.append(file_path)
                                                # logger.info(f"[Task {task_id}] ✅ File path #{len(file_paths)}: {file_path}")

                                    # logger.info(f"[Task {task_id}] ✅ Extracted {len(code_snippets)} code snippets from {len(file_paths)} files")
                                else:
                                    logger.error(f"[Task {task_id}] ❌ rag.documents not available!")
                            else:
                                logger.warning(f"[Task {task_id}] ⚠️ No doc_indices in RetrieverOutput")

                        if code_snippets:
                            relevant_code = "\n\n---\n\n".join(code_snippets[:5])  # 最多5个代码片段
                            logger.info(f"[Task {task_id}] ✅ Retrieved {len(code_snippets)} code snippets from {len(file_paths)} files for page: {page['title']}")
                        else:
                            logger.warning(f"[Task {task_id}] ⚠️ No valid code snippets extracted for page: {page['title']}")
                    else:
                        logger.warning(f"[Task {task_id}] ⚠️ Retriever returned no documents for page: {page['title']}")
                except Exception as e:
                    logger.error(f"[Task {task_id}] ❌ Failed to retrieve code for page {page['title']}: {e}", exc_info=True)
            else:
                logger.warning(f"[Task {task_id}] ⚠️ RAG retriever not available")

            # 传递文件路径给内容生成函数
            content = generate_page_content(task, page, relevant_code, file_paths)

            # ⚠️ 不再在生成时渲染 Mermaid 图表
            # Mermaid 代码块保留在 Markdown 中，由前端使用 react-markdown 处理
            # 前端会自动检测 ```mermaid ... ``` 代码块并渲染
            # content = render_mermaid_in_markdown(content, task_id=task_id)

            page['content'] = content
        except Exception as e:
            logger.error(f"[Task {task_id}] Failed to generate content for page {page['title']}: {e}")
            page['content'] = f"# {page['title']}\n\n生成失败: {str(e)}"

    # 构建最终结构
    wiki_structure = {
        'title': f"{repo_name} Wiki",
        'description': f"Documentation for {repo_name}",
        'pages': pages_structure
    }

    logger.info(f"[Task {task_id}] ✅ Wiki generation complete!")

    return wiki_structure, documents_count