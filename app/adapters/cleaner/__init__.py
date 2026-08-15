"""默认文档清洗器（入库链路：解析后、分块前）。

按序执行（每步可独立开关，config.cleaner.*）：
1. normalize   格式归一：全半角、压缩连续空白、去除控制字符。
2. noise       噪声去除：连续页码、页眉/页脚重复行、固定模板串（可配置正则）。
3. dedup       段落级近重复去重：simhash + 汉明距离阈值，保留信息最全者。
4. quality     质量过滤：丢弃过短段、去停用词后无实词的废话段。

默认 enabled=false：不改变现有入库行为（验收总原则 1）。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from app.core.interfaces import Cleaner

# 全角 → 半角（与 rule_rewriter.normalize_query 同款映射，文档侧复用）
_FULL_TO_HALF = {
    "！": "!", "？": "?", "，": ",", "。": ".", "：": ":", "；": ";", "（": "(", "）": ")",
    "“": '"', "”": '"', "‘": "'", "’": "'", "—": "-", "％": "%", "　": " ",
}

# 噪声正则（默认）：连续页码、Page N、第 N 页、纯分隔线、孤立数字行
_DEFAULT_NOISE_PATTERNS = (
    r"第\s*\d+\s*页",
    r"页\s*/\s*共?\s*\d+\s*页",
    r"\bPage\s+\d+(\s+of\s+\d+)?\b",
    r"^\s*[-—=_*]{4,}\s*$",
    r"^\s*\d{1,4}\s*$",
)

# 质量过滤：段长下限与"无实词"判定（jieba 分词后命中实词词性集合）
_MIN_CHUNK_CHARS = 20
_MEANINGFUL_POS = {"n", "nr", "ns", "nt", "nz", "v", "vn", "a", "ad", "d", "m", "q", "eng"}


def _simhash(text: str, bits: int = 64) -> int:
    """简单词加权 simhash：分词 → 各 token 哈希 → 加权位累积 → 签名。"""
    try:
        import jieba

        tokens = [t for t in jieba.cut(text) if t.strip()]
    except Exception:
        tokens = re.findall(r"[\u4e00-\u9fff]{1,4}|[a-zA-Z0-9]+", text)
    if not tokens:
        return 0
    v = [0] * bits
    for tok in tokens:
        h = int(hashlib.sha256(tok.encode("utf-8")).hexdigest()[:16], 16)
        for i in range(bits):
            v[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(bits):
        if v[i] > 0:
            out |= 1 << i
    return out


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _is_meaningful(text: str, min_chars: int = _MIN_CHUNK_CHARS) -> bool:
    """质量过滤：过短或无实词（jieba 分词词性判定）视为废话块。"""
    if len(text.strip()) < min_chars:
        return False
    try:
        import jieba.posseg as pseg

        words = [w for w in pseg.cut(text) if w.word.strip()]
        real = [w for w in words if (w.flag or "")[:1] in _MEANINGFUL_POS]
        return len(real) >= 2
    except Exception:
        # 无 jieba 时退回长度判定（不误杀）
        return True


@dataclass
class DefaultCleaner(Cleaner):
    """规则清洗器：全部步骤可独立开关；dedup 用 simhash + 汉明阈值。"""

    enabled: bool = False
    normalize: bool = True
    noise: bool = True
    dedup: bool = True
    quality: bool = True
    noise_patterns: list[str] = field(default_factory=lambda: list(_DEFAULT_NOISE_PATTERNS))
    dedup_threshold: float = 0.9      # simhash 相似度 = 1 - hamming/bits
    min_chunk_chars: int = _MIN_CHUNK_CHARS

    def __post_init__(self) -> None:
        self._noise_re = [re.compile(p) for p in self.noise_patterns]

    def clean(self, text: str) -> str:
        if not self.enabled:
            return text
        if self.normalize:
            text = self._normalize(text)
        if self.noise:
            text = self._strip_noise(text)
        paragraphs = self._split_paragraphs(text)
        if self.dedup:
            paragraphs = self._dedup_paragraphs(paragraphs)
        if self.quality:
            paragraphs = [p for p in paragraphs if _is_meaningful(p, self.min_chunk_chars)]
        return "\n\n".join(paragraphs).strip()

    # -- 步骤实现 --------------------------------------------------------------

    @staticmethod
    def _normalize(text: str) -> str:
        for full, half in _FULL_TO_HALF.items():
            text = text.replace(full, half)
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        return re.sub(r"[ \t\u3000]+", " ", text).strip()

    def _strip_noise(self, text: str) -> str:
        lines = text.split("\n")
        # 页眉/页脚检测：先统计非空短行出现次数，出现 >= 3 次的整行全部剔除
        freq: dict[str, int] = {}
        for line in lines:
            stripped = line.strip()
            if stripped and len(stripped) <= 40:
                freq[stripped] = freq.get(stripped, 0) + 1
        out: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                out.append(line)
                continue
            if any(r.search(stripped) for r in self._noise_re):
                continue
            if freq.get(stripped, 0) >= 3:
                continue
            out.append(line)
        return "\n".join(out)

    @staticmethod
    def _split_paragraphs(text: str) -> list[str]:
        return [p.strip() for p in text.split("\n\n") if p.strip()]

    def _dedup_paragraphs(self, paragraphs: list[str]) -> list[str]:
        """simhash 近重复去重：按文档顺序保留首个，相似度超阈值丢弃后续（保留信息最全者）。

        P1 修正：seen 记录 (sig, kept 索引) 而非段落文本——「B 替换 A 后，C 与 A 仍相似」
        时不再 `kept.index(旧文本)`（替换后旧文本已不存在 → ValueError），改为原地替换 kept[i]。
        """
        kept: list[str] = []
        seen: list[tuple[int, int]] = []
        bits = 64
        for para in paragraphs:
            sig = _simhash(para, bits)
            dup = False
            for prev_sig, prev_idx in seen:
                if sig and _hamming(sig, prev_sig) <= int((1 - self.dedup_threshold) * bits):
                    # 保留信息更全者：原地替换（索引稳定，不依赖文本查找）
                    if len(para) > len(kept[prev_idx]):
                        kept[prev_idx] = para
                    dup = True
                    break
            if not dup:
                kept.append(para)
                seen.append((sig, len(kept) - 1))
        return kept
