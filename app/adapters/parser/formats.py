"""解析格式常量表（DEVELOPMENT.md 5.1、第 16 章）。

纯数据模块，零依赖：供 parser 各子模块与 `config.py` 默认值引用。
"""
from __future__ import annotations


# 16.1：代码/配置类扩展名 → metadata["language"]（未知扩展名不写该键）
CODE_LANGUAGES: dict[str, str] = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript",
    ".java": "java", ".go": "go", ".rs": "rust", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp", ".rb": "ruby", ".php": "php",
    ".kt": "kotlin", ".swift": "swift", ".sh": "shell", ".sql": "sql",
    ".vue": "vue", ".css": "css", ".scss": "scss",
    ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".ini": "ini", ".xml": "xml",
}

CODE_EXTENSIONS: tuple[str, ...] = tuple(CODE_LANGUAGES)

# 16.2：旧版 Office 为 OLE2 复合文档（.doc/.xls/.ppt）
LEGACY_OFFICE_EXTENSIONS = (".doc", ".xls", ".ppt")

# 16.4：表格类文本格式（CSV/TSV），与 xlsx/xls 同口径
TABULAR_EXTENSIONS = (".csv", ".tsv")

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

SUPPORTED_EXTENSIONS = ({".pdf", ".docx", ".xlsx", ".pptx", ".md", ".txt",
                         ".html", ".htm", ".eml", ".msg", ".json", ".jsonl"}
                        | set(LEGACY_OFFICE_EXTENSIONS)
                        | set(TABULAR_EXTENSIONS)
                        | set(CODE_EXTENSIONS))
