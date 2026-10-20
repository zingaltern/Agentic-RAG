"""代码文件解析（DEVELOPMENT.md 16.1 / 16.5）：tree-sitter AST 符号提取 + 纯文本回退。

- **有 grammar 的语言**：tree-sitter 解析原始字节 → 提取函数/类/方法等定义
  （name / kind / 起止行号）→ 写入 `metadata["symbols"]` 与 `symbol_count`，
  并按 `ingestion.code_symbols.index_in_text` 在正文前置 `[CODE_INDEX]` 符号索引块；
- **无 grammar 或 tree-sitter 未安装**：回退纯文本解码（与 16.1 原行为一致，不报错）；
- 只做解析与统一化：**不重写源码、不做清洗**（清洗归 `app/adapters/cleaner/`）。

grammar 包按需 import（`tree_sitter_python` 等），缺包只影响该语言，不影响整体启动。
"""
from __future__ import annotations

import importlib
from typing import Callable

from app.adapters.parser.common import _decode_text
from app.adapters.parser.formats import CODE_LANGUAGES
from app.core.logging import get_logger

logger = get_logger("parser")

# 扩展名 → (grammar 语言名, grammar 模块, 工厂函数)；未列出的扩展名走纯文本回退
_GRAMMAR_MODULES: dict[str, tuple[str, str, str]] = {
    ".py": ("python", "tree_sitter_python", "language"),
    ".js": ("javascript", "tree_sitter_javascript", "language"),
    ".mjs": ("javascript", "tree_sitter_javascript", "language"),
    ".cjs": ("javascript", "tree_sitter_javascript", "language"),
    ".jsx": ("javascript", "tree_sitter_javascript", "language"),
    ".ts": ("typescript", "tree_sitter_typescript", "language_typescript"),
    ".tsx": ("tsx", "tree_sitter_typescript", "language_tsx"),
    ".java": ("java", "tree_sitter_java", "language"),
    ".go": ("go", "tree_sitter_go", "language"),
    ".rs": ("rust", "tree_sitter_rust", "language"),
    ".c": ("c", "tree_sitter_c", "language"),
    ".h": ("c", "tree_sitter_c", "language"),
    ".cpp": ("cpp", "tree_sitter_cpp", "language"),
    ".hpp": ("cpp", "tree_sitter_cpp", "language"),
    ".cs": ("c_sharp", "tree_sitter_c_sharp", "language"),
    ".rb": ("ruby", "tree_sitter_ruby", "language"),
    ".php": ("php", "tree_sitter_php", "language_php"),
    ".kt": ("kotlin", "tree_sitter_kotlin", "language"),
    ".swift": ("swift", "tree_sitter_swift", "language"),
    ".sh": ("bash", "tree_sitter_bash", "language"),
    ".css": ("css", "tree_sitter_css", "language"),
}

# grammar 语言名 → 定义节点类型映射（节点类型 → 符号种类）
_DEFINITIONS: dict[str, dict[str, str]] = {
    "python": {"class_definition": "class", "function_definition": "function"},
    "javascript": {"class_declaration": "class", "function_declaration": "function",
                   "method_definition": "method"},
    "typescript": {"class_declaration": "class", "function_declaration": "function",
                   "method_definition": "method", "interface_declaration": "interface",
                   "type_alias_declaration": "type", "enum_declaration": "enum"},
    "tsx": {"class_declaration": "class", "function_declaration": "function",
            "method_definition": "method", "interface_declaration": "interface",
            "type_alias_declaration": "type", "enum_declaration": "enum"},
    "java": {"class_declaration": "class", "interface_declaration": "interface",
             "enum_declaration": "enum", "record_declaration": "record",
             "method_declaration": "method", "constructor_declaration": "constructor"},
    "go": {"function_declaration": "function", "method_declaration": "method",
           "type_declaration": "type"},
    "rust": {"function_item": "function", "struct_item": "struct", "enum_item": "enum",
             "trait_item": "trait", "impl_item": "impl", "mod_item": "module"},
    "c": {"function_definition": "function", "struct_specifier": "struct",
          "enum_specifier": "enum", "type_definition": "typedef"},
    "cpp": {"function_definition": "function", "class_specifier": "class",
            "struct_specifier": "struct", "namespace_definition": "namespace",
            "enum_specifier": "enum"},
    "c_sharp": {"class_declaration": "class", "interface_declaration": "interface",
                "struct_declaration": "struct", "enum_declaration": "enum",
                "namespace_declaration": "namespace", "method_declaration": "method",
                "property_declaration": "property"},
    "ruby": {"class": "class", "module": "module", "method": "method",
             "singleton_method": "method"},
    "php": {"class_declaration": "class", "interface_declaration": "interface",
            "trait_declaration": "trait", "function_definition": "function",
            "method_declaration": "method"},
    "kotlin": {"class_declaration": "class", "object_declaration": "object",
               "function_declaration": "function"},
    "swift": {"class_declaration": "class", "struct_declaration": "struct",
              "protocol_declaration": "protocol", "enum_declaration": "enum",
              "actor_declaration": "actor", "function_declaration": "function"},
    "bash": {"function_definition": "function"},
    "css": {"rule_set": "rule"},
}

# 可作为「容器」的符号种类（其内部方法名会加容器前缀，如 Foo.bar）
_CONTAINER_KINDS = {"class", "struct", "trait", "interface", "impl", "module",
                    "namespace", "object", "protocol", "enum", "record", "actor"}

# 名称节点类型（各 grammar 的叫法不统一）
_IDENTIFIER_TYPES = {"identifier", "type_identifier", "field_identifier",
                     "property_identifier", "simple_identifier", "constant", "name"}

_GRAMMAR_CACHE: dict[str, object | None] = {}


def _grammar_language(ext: str) -> str:
    """扩展名 → grammar 语言名（无 grammar 返回 "text"）。"""
    spec = _GRAMMAR_MODULES.get(ext)
    return spec[0] if spec else "text"


def language_extensions() -> dict[str, tuple[str, ...]]:
    """按 grammar 语言名聚合扩展名（注册用）；无 grammar 的归入 "text"。"""
    grouped: dict[str, list[str]] = {}
    for ext in CODE_LANGUAGES:
        grouped.setdefault(_grammar_language(ext), []).append(ext)
    return {lang: tuple(exts) for lang, exts in grouped.items()}


def _load_grammar(language: str) -> object | None:
    """按需加载 grammar（结果缓存）；未安装/加载失败 → None（回退纯文本）。"""
    if language in _GRAMMAR_CACHE:
        return _GRAMMAR_CACHE[language]
    loaded: object | None = None
    spec = next((s for s in _GRAMMAR_MODULES.values() if s[0] == language), None)
    if spec is not None:
        module_name, factory = spec[1], spec[2]
        try:
            from tree_sitter import Language

            module = importlib.import_module(module_name)
            loaded = Language(getattr(module, factory)())
        except Exception as exc:  # noqa: BLE001 —— 缺包/版本不符都回退纯文本
            logger.debug(f"代码 grammar 不可用（{language}），回退纯文本：{exc}")
            loaded = None
    _GRAMMAR_CACHE[language] = loaded
    return loaded


def _first_identifier(node: object) -> object | None:
    """深度优先找第一个标识符节点（C/C++ 声明符、Go type_spec、CSS selectors 等无 name 字段时兜底）。"""
    if getattr(node, "type", "") in _IDENTIFIER_TYPES:
        return node
    for child in getattr(node, "children", []):
        found = _first_identifier(child)
        if found is not None:
            return found
    return None


def _node_name(node: object, source: bytes) -> str | None:
    """取定义节点名称：优先 name 字段，其次 declarator/selectors 等，最后兜底首个标识符。"""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        for field in ("declarator", "selectors", "type", "property"):
            sub = node.child_by_field_name(field)
            if sub is not None:
                name_node = _first_identifier(sub)
                if name_node is not None:
                    break
    if name_node is None:
        name_node = _first_identifier(node)
    if name_node is None:
        return None
    raw = source[name_node.start_byte:name_node.end_byte]
    name = raw.decode("utf-8", errors="replace").strip()
    if not name:
        return None
    return name if len(name) <= 120 else name[:117] + "..."      # CSS 选择器等可能很长


def _extract_symbols(source: bytes, tree: object, definitions: dict[str, str],
                     max_symbols: int) -> list[dict]:
    """深度优先遍历 AST 提取定义符号（含嵌套方法，方法名带容器前缀）。"""
    symbols: list[dict] = []

    def walk(node: object, container: str | None) -> None:
        if len(symbols) >= max_symbols:
            return
        kind = definitions.get(getattr(node, "type", ""))
        name = _node_name(node, source) if kind else None
        if kind and name:
            if container and kind == "function":
                kind = "method"          # 类/结构体内的函数即方法（Python 等无独立 method 节点）
            symbols.append({
                "name": f"{container}.{name}" if container and kind in ("method", "constructor", "property") else name,
                "kind": kind,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
            })
        next_container = container
        if kind in _CONTAINER_KINDS and name:
            next_container = f"{container}.{name}" if container else name
        for child in getattr(node, "children", []):
            walk(child, next_container)

    walk(tree.root_node, None)
    return symbols


def _render_symbol_index(language: str, symbols: list[dict]) -> str:
    """`[CODE_INDEX]` 符号索引块（与 [TABLE] 块同为机器可识别的原子块）。"""
    lines = [f"[CODE_INDEX language={language} symbols={len(symbols)}]"]
    lines.extend(f"{s['kind']} {s['name']} (L{s['line_start']}-L{s['line_end']})"
                 for s in symbols)
    lines.append("[/CODE_INDEX]")
    return "\n".join(lines)


def _parse_code(raw: bytes, config: object | None = None,
                language: str = "text") -> tuple[str, dict]:
    """代码文件解析：AST 符号提取（可用配置关闭/回退）。"""
    text = _decode_text(raw)
    meta: dict = {"page": None, "author": None}
    cfg = getattr(config, "code_symbols", None)
    if cfg is not None and not bool(getattr(cfg, "enabled", True)):
        return text, meta

    definitions = _DEFINITIONS.get(language)
    grammar = _load_grammar(language) if definitions else None
    if grammar is None or not definitions:
        return text, meta                       # 无 grammar → 纯文本回退（零行为变化）

    try:
        from tree_sitter import Parser

        tree = Parser(grammar).parse(raw)
    except Exception as exc:  # noqa: BLE001 —— 解析器异常同样回退，不阻塞入库
        logger.warning(f"tree-sitter 解析失败（{language}），回退纯文本：{exc}")
        return text, meta

    max_symbols = int(getattr(cfg, "max_symbols", 500)) if cfg is not None else 500
    symbols = _extract_symbols(raw, tree, definitions, max(1, max_symbols))
    if not symbols:
        return text, meta

    meta["symbols"] = symbols
    meta["symbol_count"] = len(symbols)
    index_in_text = bool(getattr(cfg, "index_in_text", True)) if cfg is not None else True
    if index_in_text:
        return _render_symbol_index(language, symbols) + "\n" + text, meta
    return text, meta


def make_code_parser(language: str) -> Callable[[bytes, object | None], tuple[str, dict]]:
    """生成绑定到某语言的解析函数（注册表用；fn 签名与其它解析器一致）。"""

    def _parse(raw: bytes, config: object | None = None) -> tuple[str, dict]:
        return _parse_code(raw, config, language)

    _parse.__name__ = f"_parse_code_{language}"
    _parse.__qualname__ = _parse.__name__
    return _parse
