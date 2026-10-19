"""父子分块（DEVELOPMENT.md 5.2）。

契约（3.9）：`split_chunks(doc: Document) -> list[Chunk]`
规则：
1. 以自然段落/行为切割单元聚合成父块，块内以 `\n` 连接（保留行结构）；
   段落超目标按 `。！？；` 与英文 `.?!`（空白后断句）切句，禁止在词中断（尽量在空白处断）。
2. 父块 ≤ parent_target_tokens（按 `token_counter` 精确计数，缺省回退 ratio 估算）；
   父块再按 child_target_tokens 切子块。父块本身未超子块目标时不再产出重复子块。
3. 父块 parent_id=None；子块 parent_id=父块 chunk_id。
4. chunk_id = sha256(document_id + ":" + str(order))[:16]（2.1）。

V5.2：token 计量支持注入 `token_counter`（引擎可选能力，如 BGE 真实分词器）——
不注入时行为与旧版完全一致（ratio 估算）；计量口径变化由索引 DNA `pipeline_version` 兜底。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Callable

from app.core.domain import Chunk, Document
from app.core.logging import get_logger

logger = get_logger("chunker")

_SENTENCE_END = "。！？；"
# 英文断句：句末标点后跟空白才切（避免 3.14 / v2.1 等误切）
_EN_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _estimate_tokens(text: str, token_char_ratio: float) -> int:
    """中文 token 估算：len(text)/token_char_ratio 向上取整（5.2；回退口径）。"""
    return max(1, math.ceil(len(text) / token_char_ratio))


def _make_counter(token_char_ratio: float,
                  token_counter: Callable[[str], int] | None) -> Callable[[str], int]:
    """构造计数函数：优先 token_counter（精确），异常/缺失/非法返回时回退 ratio 估算。"""
    def _count(text: str) -> int:
        if token_counter is not None:
            try:
                value = token_counter(text)
                if isinstance(value, int) and value > 0:
                    return value
            except Exception:  # noqa: BLE001 计数失败不阻塞入库（回退估算）
                logger.warning("token_counter 调用失败，回退 ratio 估算")
        return _estimate_tokens(text, token_char_ratio)
    return _count


def _split_sentences(text: str) -> list[str]:
    """按句末标点切句（保留标点）：中文标点 + 英文句末（空白后）双口径，丢弃空白段。"""
    out: list[str] = []
    for part in re.split(rf"(?<=[{_SENTENCE_END}])", text):
        out.extend(p.strip() for p in _EN_SENTENCE_SPLIT.split(part) if p.strip())
    return out


def _hard_cut(text: str, limit_chars: int) -> tuple[str, str]:
    """超长单句硬切：优先在窗口内最后一个空白处断开（避免断词），否则按字符边界。"""
    head, tail = text[:limit_chars], text[limit_chars:]
    space = head.rfind(" ")
    if 0 < space and space >= limit_chars * 0.6:
        head, tail = head[:space], head[space:] + tail
    return head, tail


def _split_overlong(text: str, target_tokens: int, count: Callable[[str], int],
                    token_char_ratio: float) -> list[str]:
    """把超目标的单句切成 ≤ target_tokens 的若干段（按 count 计量，硬切窗口按 ratio 折算）。"""
    limit_chars = max(1, int(target_tokens * token_char_ratio))
    parts: list[str] = []
    while count(text) > target_tokens and len(text) > limit_chars:
        head, text = _hard_cut(text, limit_chars)
        head = head.strip()
        if head:
            parts.append(head)
    text = text.strip()
    if text:
        parts.append(text)
    return parts


def _group_to_target(segments: list[str], target_tokens: int,
                     count: Callable[[str], int],
                     token_char_ratio: float) -> list[str]:
    """把句子序列按目标 token 数聚合成块（超长单句先硬切，返回文本列表）。"""
    expanded: list[str] = []
    for seg in segments:
        if count(seg) > target_tokens:
            expanded.extend(_split_overlong(seg, target_tokens, count, token_char_ratio))
        else:
            expanded.append(seg)
    groups: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for seg in expanded:
        seg_tokens = count(seg)
        if current and current_tokens + seg_tokens > target_tokens:
            groups.append("".join(current))
            current, current_tokens = [], 0
        current.append(seg)
        current_tokens += seg_tokens
    if current:
        groups.append("".join(current))
    return groups


def _build_line_sections(text: str, sections: list) -> list:
    """T5：把 [(title, 行号)] 展开为逐行 section 归属（标题行起到下一标题行前）。"""
    lines = text.split("\n")
    line_section: list = [None] * len(lines)
    for idx, (title, line_no) in enumerate(sections):
        if not (0 <= line_no < len(lines)):
            continue
        next_no = sections[idx + 1][1] if idx + 1 < len(sections) else len(lines)
        for i in range(line_no, min(next_no, len(lines))):
            line_section[i] = title
    return line_section


def _markdown_lines(block: str) -> tuple[str, str, list[str]]:
    """从 [TABLE] 块文本提取 (表头行, 分隔行, 数据行)——均为原始管道行文本。"""
    md_lines = [ln for ln in block.split("\n") if ln.strip().startswith("|")]
    if len(md_lines) < 2:
        return "", "", []
    return md_lines[0], md_lines[1], md_lines[2:]


def _chunk_id(document_id: str, order: int) -> str:
    return hashlib.sha256(f"{document_id}:{order}".encode("utf-8")).hexdigest()[:16]


def split_chunks(
    doc: Document,
    parent_target_tokens: int = 512,
    child_target_tokens: int = 128,
    token_char_ratio: float = 1.8,
    table_aware: dict | None = None,
    hard_boundary: dict | None = None,
    context_prefix: dict | None = None,
    token_counter: Callable[[str], int] | None = None,
) -> list[Chunk]:
    """按父子分块规则产出一组 Chunk（纯函数；配置缺省值与 settings.yaml 一致）。

    P1-4：table_aware={"enabled", "max_rows_per_chunk", "json_max_bytes"}——[TABLE] 原子块。
    P1-5：hard_boundary={"enabled"}——聚合遇 section 归属变更强制断块；
          context_prefix={"enabled", "template", "include_page"}——chunk.text 加
          `{doc_title} > {section}` 上下文前缀（不计切块 token、计 embedding，>64 token 截断），
          并按 doc.metadata["page_spans"] 写 chunk 级 page。
    V5.2：token_counter（可选）——精确 token 计数；缺省/异常回退 token_char_ratio 估算。
    """
    text = (doc.text or "").strip()
    if not text:
        return []
    count = _make_counter(token_char_ratio, token_counter)
    chunks: list[Chunk] = []
    order = 0
    ta_enabled = bool(table_aware and table_aware.get("enabled"))
    max_rows = int((table_aware or {}).get("max_rows_per_chunk") or 30)
    json_max_bytes = int((table_aware or {}).get("json_max_bytes") or 4096)
    hb_enabled = bool(hard_boundary and hard_boundary.get("enabled"))
    cp_enabled = bool(context_prefix and context_prefix.get("enabled"))
    cp_template = (context_prefix or {}).get("template") or "{doc_title} > {section}"
    cp_include_page = bool((context_prefix or {}).get("include_page"))
    doc_title = doc.title or doc.filename

    # T5：章节标题 → 逐行 section 归属（sections 为解析器产出的 [(title, 行号)]）
    line_section: list = []
    sections_meta = doc.metadata.get("sections") or []
    if sections_meta:
        line_section = _build_line_sections(text, sections_meta)

    def section_of(line_no: int) -> str | None:
        if not line_section:
            return None
        return line_section[line_no] if 0 <= line_no < len(line_section) else None

    # P1-4/P1-5：文档级探针/映射键不随 chunk 落库（text_stats 解析事实、tables 由
    # table_json 承载、sections 由 section 键承载、page_spans 由 page 键承载）
    base_meta = {k: v for k, v in doc.metadata.items()
                 if k not in ("sections", "text_stats", "tables", "page_spans")}

    tables_meta = doc.metadata.get("tables") or []
    page_spans = doc.metadata.get("page_spans") or []   # P1-5：[[start_line, end_line, page], ...]

    def page_of(line_no: int) -> int | None:
        """P1-5：按段落起始行号查页码（查不到返回 None，不写 page 键）。"""
        for s, e, pg in page_spans:
            if s <= line_no <= e:
                return pg
        return None

    def build_prefix(section: str | None, page: int | None) -> str | None:
        """P1-5：上下文前缀（无 section 降级为仅 doc_title；>64 token 截断 + warning）。"""
        if not cp_enabled:
            return None
        prefix = cp_template.format(doc_title=doc_title, section=section) if section else doc_title
        if cp_include_page and page:
            prefix = f"{prefix} (p{page})"
        if count(prefix) > 64:
            prefix = prefix[:max(1, int(64 * token_char_ratio))].rstrip()
            logger.warning(
                "上下文前缀超 64 token 截断（防挤占嵌入模型输入上限）",
                extra={"extra_fields": {"document_id": doc.document_id}},
            )
        return prefix

    def emit(text: str, parent_id: str | None, section: str | None,
             extra: dict | None = None, page: int | None = None) -> str:
        nonlocal order
        meta = dict(base_meta)
        if section is not None:
            meta["section"] = section         # T5：章节标题落库（可参与 filters 等值过滤）
        if page is not None:
            meta["page"] = page               # P1-5：chunk 级页码溯源
        if extra:
            meta.update(extra)
        prefix = build_prefix(section, page)
        if prefix:
            text = f"{prefix}\n{text}"
        chunk = Chunk(
            chunk_id=_chunk_id(doc.document_id, order),
            document_id=doc.document_id,
            text=text,
            order=order,
            parent_id=parent_id,
            metadata=meta,
        )
        chunks.append(chunk)
        order += 1
        return chunk.chunk_id

    def emit_table(block: str, start_line: int, ordinal: int) -> None:
        """P1-4：表格原子块 → 独立父块（不与正文聚合、不产子块）。

        整表 ≤ 父块目标 → 单块携带 table_json；超限 → 行组切（每组强制携带完整表头）。
        """
        section = section_of(start_line)
        page = page_of(start_line)
        table_meta = tables_meta[ordinal] if ordinal < len(tables_meta) else None
        extra: dict = {"chunk_type": "table"}
        if table_meta is not None:
            table_json = json.dumps(table_meta, ensure_ascii=False)
            if len(table_json.encode("utf-8")) > json_max_bytes:
                table_json = table_json.encode("utf-8")[:json_max_bytes].decode("utf-8", "ignore")
                logger.warning(
                    "table_json 超限截断（table_json 不完整，仅保留表头附近的行数据）",
                    extra={"extra_fields": {"document_id": doc.document_id,
                                            "table_index": ordinal,
                                            "json_max_bytes": json_max_bytes}},
                )
            extra["table_json"] = table_json
        rows = _markdown_lines(block)
        if count(block) <= parent_target_tokens or len(rows) <= 1:
            emit(block, None, section, extra, page=page)
            return
        header_line, sep_line, data_lines = rows
        groups: list[list[str]] = [data_lines[i:i + max_rows]
                                   for i in range(0, len(data_lines), max_rows)]
        total = len(groups)
        for gi, group_rows in enumerate(groups, 1):
            part_lines = [header_line, sep_line, *group_rows]
            part_extra = dict(extra)
            if total > 1:
                part_extra["table_part"] = f"{gi}/{total}"
            emit("\n".join(part_lines), None, section, part_extra, page=page)

    # 1) 以自然段落（换行分隔）聚合成父块（记录每段起始行号，用于 section 归属）；
    #    P1-4：table_aware 开启时 [TABLE]..[/TABLE] 行域预合并为表格原子单元
    raw_lines = text.split("\n")
    items: list[tuple[int, str, bool]] = []      # (起始行号, 文本, 是否表格原子)
    if ta_enabled:
        i = 0
        while i < len(raw_lines):
            if raw_lines[i].strip() == "[TABLE]":
                j = i
                while j < len(raw_lines) and raw_lines[j].strip() != "[/TABLE]":
                    j += 1
                block = "\n".join(raw_lines[i:min(j + 1, len(raw_lines))]).strip()
                if block:
                    items.append((i, block, True))
                i = j + 1
                continue
            if raw_lines[i].strip():
                items.append((i, raw_lines[i].strip(), False))
            i += 1
    else:
        items = [(i, line.strip(), False) for i, line in enumerate(raw_lines) if line.strip()]

    parents: list[tuple[str, int, bool, int]] = []   # (text, start_line, 是否表格, 表格序数)
    current: list[str] = []
    current_start = 0
    current_tokens = 0
    current_section: str | None = None
    table_ordinal = 0
    for line_no, para, is_table in items:
        if is_table:
            if current:
                parents.append(("\n".join(current), current_start, False, -1))
                current, current_tokens = [], 0
            parents.append((para, line_no, True, table_ordinal))
            table_ordinal += 1
            continue
        para_tokens = count(para)
        # P1-5：标题硬边界——section 归属变更即断块（同 section 连续内容不受影响）
        para_section = section_of(line_no)
        if current and hb_enabled and para_section != current_section:
            parents.append(("\n".join(current), current_start, False, -1))
            current, current_tokens = [], 0
        # V5.2：加入下一段后的 token（含 1 个换行分隔符）不得超父块目标
        add_tokens = para_tokens + (1 if current else 0)
        if current and current_tokens + add_tokens > parent_target_tokens:
            parents.append(("\n".join(current), current_start, False, -1))
            current, current_tokens = [], 0
        if para_tokens > parent_target_tokens:
            if current:
                parents.append(("\n".join(current), current_start, False, -1))
                current, current_tokens = [], 0
            for group in _group_to_target(_split_sentences(para), parent_target_tokens,
                                          count, token_char_ratio):
                parents.append((group, line_no, False, -1))
        else:
            if not current:
                current_start = line_no
                current_section = para_section
            current.append(para)
            current_tokens += para_tokens + (1 if len(current) > 1 else 0)
    if current:
        parents.append(("\n".join(current), current_start, False, -1))

    # 2) 按文档顺序产出：正文父块（+子块）与表格块统一编号（P1-4 顺序保序）
    for parent_text, start_line, is_table, ordinal in parents:
        parent_text = parent_text.strip()
        if not parent_text:
            continue
        section = section_of(start_line)
        page = page_of(start_line)
        if is_table:
            emit_table(parent_text, start_line, ordinal)
            continue
        parent_chunk_id = emit(parent_text, None, section, page=page)
        if count(parent_text) <= child_target_tokens:
            continue  # 父块本身已在子块目标内，不产出重复子块
        for group in _group_to_target(_split_sentences(parent_text), child_target_tokens,
                                      count, token_char_ratio):
            emit(group, parent_chunk_id, section, page=page)

    return chunks
