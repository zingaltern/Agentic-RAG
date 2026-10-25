"""默认文档清洗器（入库链路：解析后、分块前）。

按序执行（每步可独立开关，config.cleaner.*）：
1. normalize   格式归一：全角字母数字/表意空格/控制字符（V5.2 拆两档：
               宽度归一默认开；中文标点→半角 ASCII 由 `normalize_punct` 控制，默认关）。
2. noise       噪声去除：连续页码、页眉/页脚重复行、固定模板串（可配置正则）。
3. dedup       段落级近重复去重：simhash + 汉明距离阈值，保留信息最全者。
4. quality     质量过滤：丢弃过短段、去停用词后无实词的废话段。

V5.2 增强（全部向后兼容，默认值保持/可回退）：
- 分段 `paragraph_mode=auto`：空行优先；整篇无空行（PDF/纯行文本）时按行细分，
  修复"整篇=一段"导致 dedup/quality 失效；[TABLE] 原子块始终独立成段。
- 按类型豁免 `kind`（代码/JSON/CSV/表格类）：跳过 quality/dedup，防误杀。
- 安全阀：`max_drop_ratio`（质量过滤删除占比超限则整体跳过）；
  `dedup_max_paragraphs`（段落数超限跳过 dedup，防 O(n²) 卡死）。

默认 enabled=false：不改变现有入库行为（验收总原则 1）。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from app.core.interfaces import Cleaner
from app.core.logging import get_logger

logger = get_logger("cleaner")

# 宽度归一（默认开）：全角字母/数字 → 半角；表意空格 → 普通空格；全角百分号 → 半角
_WIDTH_MAP = {
    **{chr(0xFF10 + i): chr(ord("0") + i) for i in range(10)},      # ０-９
    **{chr(0xFF21 + i): chr(ord("A") + i) for i in range(26)},      # Ａ-Ｚ
    **{chr(0xFF41 + i): chr(ord("a") + i) for i in range(26)},      # ａ-ｚ
    "％": "%", "　": " ",
}

# 中文标点 → 半角 ASCII（V5.2：独立开关 normalize_punct，默认关——避免改写中文排版）
_PUNCT_MAP = {
    "！": "!", "？": "?", "，": ",", "。": ".", "：": ":", "；": ";",
    "（": "(", "）": ")", "“": '"', "”": '"', "‘": "'", "’": "'", "—": "-",
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

# P1-4：[TABLE] 原子块（捕获组分割，保序保边界）
_TABLE_BLOCK_RE = re.compile(r"(\[TABLE\]\n.*?\n\[/TABLE\])", flags=re.S)


def _split_table_segments(text: str) -> list[tuple[bool, str]]:
    """把文本切成 [(是否表格块, 内容)] 序列（拼接后与原文等价，P1-4 表格豁免用）。"""
    out: list[tuple[bool, str]] = []
    for part in _TABLE_BLOCK_RE.split(text):
        if part:
            out.append((part.startswith("[TABLE]"), part))
    return out


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


def _normalize_text(text: str, punct: bool) -> str:
    """格式归一：宽度（默认）+ 可选中文标点→半角；去除控制字符并压缩空白。"""
    for full, half in _WIDTH_MAP.items():
        text = text.replace(full, half)
    if punct:
        for full, half in _PUNCT_MAP.items():
            text = text.replace(full, half)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


@dataclass
class DefaultCleaner(Cleaner):
    """规则清洗器：全部步骤可独立开关；dedup 用 simhash + 汉明阈值。"""

    enabled: bool = False
    normalize: bool = True
    normalize_punct: bool = False     # V5.2：中文标点→半角 ASCII（默认关）
    noise: bool = True
    dedup: bool = True
    quality: bool = True
    noise_patterns: list[str] = field(default_factory=lambda: list(_DEFAULT_NOISE_PATTERNS))
    dedup_threshold: float = 0.9      # simhash 相似度 = 1 - hamming/bits
    min_chunk_chars: int = _MIN_CHUNK_CHARS
    paragraph_mode: str = "auto"      # auto|blank|line（V5.2）
    max_drop_ratio: float = 0.5       # V5.2：质量过滤删除占比超限则跳过
    dedup_max_paragraphs: int = 5000  # V5.2：超限跳过 dedup
    exempt_kinds: tuple[str, ...] = ()  # V5.2：豁免 quality/dedup 的文档类型

    def __post_init__(self) -> None:
        self._noise_re = [re.compile(p) for p in self.noise_patterns]

    def clean(self, text: str, kind: str | None = None) -> str:
        if not self.enabled:
            return text
        exempt = bool(kind) and kind in set(self.exempt_kinds)
        # P1-4：normalize / noise 仅作用于非 [TABLE] 段（表格内容白名单豁免——
        # 全角转半角等改写会让 tables JSON 与正文错位，且单元格内容无噪声可言）
        rebuilt: list[str] = []
        for is_table, seg in _split_table_segments(text):
            if is_table:
                rebuilt.append(seg)
                continue
            if self.normalize:
                seg = self._normalize_preserve_breaks(seg)
            if self.noise:
                seg = self._strip_noise(seg)
            rebuilt.append(seg)
        text = "".join(rebuilt)
        paragraphs = self._split_paragraphs(text)
        if self.dedup and not exempt:
            if len(paragraphs) > self.dedup_max_paragraphs:
                logger.warning(
                    "段落数 %d 超过 dedup 上限 %d，跳过近重复去重（防 O(n²) 卡死）",
                    len(paragraphs), self.dedup_max_paragraphs,
                )
            else:
                paragraphs = self._dedup_paragraphs(paragraphs)
        if self.quality and not exempt:
            # P1-4：含 [TABLE] 的段落豁免质量过滤（分段粒度下表格页可能被判"无实词"）
            kept = [p for p in paragraphs
                    if "[TABLE]" in p or _is_meaningful(p, self.min_chunk_chars)]
            # V5.2 安全阀：删除占比超限说明判定过于激进 → 整体跳过并告警
            total_chars = sum(len(p) for p in paragraphs) or 1
            dropped = total_chars - sum(len(p) for p in kept)
            if dropped / total_chars > self.max_drop_ratio:
                logger.warning(
                    "质量过滤删除占比 %.0f%% 超过上限 %.0f%%，整体跳过质量过滤",
                    100 * dropped / total_chars, 100 * self.max_drop_ratio,
                )
            else:
                paragraphs = kept
        return "\n\n".join(paragraphs).strip()

    # -- 步骤实现 --------------------------------------------------------------

    def _normalize_preserve_breaks(self, text: str) -> str:
        """归一化但保留段首/段尾换行（P1-4：分段清洗时不得吞掉与 [TABLE] 块的边界换行）。"""
        lead = text[:len(text) - len(text.lstrip("\n"))]
        trail = text[len(text.rstrip("\n")):]
        body = _normalize_text(text.strip("\n"), punct=self.normalize_punct)
        return lead + body + trail

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

    def _split_paragraphs(self, text: str) -> list[str]:
        """V5.2 分段：表格块独立成段；普通文本按模式分段。

        - blank：仅按空行分段（旧行为）；
        - line：按行分段；
        - auto：空行优先；整段无空行且行数 ≥3 时按行细分（修复"整篇=一段"）。
        """
        paragraphs: list[str] = []
        for is_table, seg in _split_table_segments(text):
            if is_table:
                stripped = seg.strip()
                if stripped:
                    paragraphs.append(stripped)
                continue
            paragraphs.extend(self._split_plain(seg))
        return paragraphs

    def _split_plain(self, seg: str) -> list[str]:
        if self.paragraph_mode == "line":
            return [ln.strip() for ln in seg.split("\n") if ln.strip()]
        blocks = [p.strip() for p in seg.split("\n\n") if p.strip()]
        if self.paragraph_mode == "auto" and len(blocks) == 1 and seg.count("\n") >= 2:
            return [ln.strip() for ln in seg.split("\n") if ln.strip()]
        return blocks

    def _dedup_paragraphs(self, paragraphs: list[str]) -> list[str]:
        """simhash 近重复去重：按文档顺序保留首个，相似度超阈值丢弃后续（保留信息最全者）。

        P1 修正：seen 记录 (sig, kept 索引) 而非段落文本——「B 替换 A 后，C 与 A 仍相似」
        时不再 `kept.index(旧文本)`（替换后旧文本已不存在 → ValueError），改为原地替换 kept[i]。
        """
        kept: list[str] = []
        seen: list[tuple[int, int]] = []
        bits = 64
        for para in paragraphs:
            if "[TABLE]" in para:
                kept.append(para)   # P1-4：含表格段落豁免去重（simhash 会把表格页误判近重复）
                continue
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
