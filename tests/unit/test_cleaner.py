"""数据清洗层单测（DEVELOPMENT.md 5.1 扩展）：归一/去噪/段落去重/质量过滤，确定性无外部依赖。"""
from __future__ import annotations

from app.adapters.cleaner import DefaultCleaner


def _clen(**kw) -> DefaultCleaner:
    base = dict(enabled=True)
    base.update(kw)
    return DefaultCleaner(**base)


def test_disabled_returns_text_unchanged():
    c = DefaultCleaner(enabled=False)
    text = "第 1 页\n\n内容保持不变。"
    assert c.clean(text) == text, "默认关：必须零副作用"


def test_normalize_halfwidth_and_whitespace():
    c = _clen(dedup=False, quality=False)
    out = c.clean("　年假申请？  \n\n公司制度：\u3000报销规则。")
    assert "?" in out and ":" in out, "全角问号/冒号应转为半角"
    assert "？" not in out and "：" not in out
    assert "\u3000" not in out


def test_noise_removes_page_numbers_and_rulers():
    c = _clen(dedup=False, quality=False)
    text = "公司制度\n\n第 3 页\n\n年假规定如下。\n\n----\n\nPage 4 of 10\n\n报销规则如下。"
    out = c.clean(text)
    assert "第 3 页" not in out
    assert "Page 4 of 10" not in out
    assert "----" not in out
    assert "年假规定如下." in out, "全角句号归一为半角"


def test_noise_removes_repeated_header_lines():
    c = _clen(dedup=False, quality=False)
    text = "机密文件-内部使用\n机密文件-内部使用\n机密文件-内部使用\n\n正文字段。"
    out = c.clean(text)
    assert "机密文件" not in out, "重复页眉行应被去除"
    assert "正文字段." in out, "全角句号归一为半角"


def test_quality_drops_short_or_noise_paragraphs():
    c = _clen(dedup=False, quality=False)
    # 单独测 quality 步骤：短段被过滤
    c2 = _clen(dedup=False, quality=True, min_chunk_chars=20)
    text = "短\n\n这是一个足够长的段落，包含多个实词，用于测试质量过滤逻辑是否正常工作。"
    out = c2.clean(text)
    assert "短" not in out
    assert "足够长的段落" in out


def test_dedup_removes_near_duplicate_paragraphs():
    c = _clen(noise=False, quality=False, dedup=True, dedup_threshold=0.9)
    a = "员工手册规定年假申请需提前三个工作日通过OA系统提交并经部门负责人审批后生效。"
    b = "员工手册规定年假申请需提前三个工作日通过OA系统提交并经部门负责人审批后生效。"  # 完全重复
    text = f"{a}\n\n{b}\n\n{text_other()}"
    out = c.clean(text)
    assert out.count("员工手册规定年假申请") == 1, "完全重复段落应去重"


def text_other() -> str:
    return "出差补贴标准为一线城市每日一百五十元，其他城市每日一百元，住宿凭发票实报实销。"


def test_dedup_keeps_longer_variant():
    c = _clen(noise=False, quality=False, dedup=True, dedup_threshold=0.9)
    short = "年假需提前三个工作日申请并审批。"
    long = "年假需提前三个工作日申请并审批，病假需在返岗后三个工作日内补交证明，事假提前两天申请。"
    # 近重复（前缀相同）→ 保留较长者
    text = f"{long}\n\n{short}"
    out = c.clean(text)
    assert "补交证明" in out, "应保留信息更全的长段落"


def test_dedup_no_crash_after_replace_then_third_similar():
    """P1 回归：B 替换 A 后，C 与 A 仍相似——不得因 kept.index(旧文本) 找不到而崩溃。"""
    c = _clen(noise=False, quality=False, dedup=True, dedup_threshold=0.9)
    a = "prefix word2 alpha beta gamma delta epsilon zeta eta theta iota kappa"
    b = a + " extra"     # 与 a 相似且更长 → 替换 a
    d = a + " more"      # 与 a（已被替换）仍相似 → 旧实现此处 ValueError
    out = c.clean(f"{a}\n\n{b}\n\n{d}")
    assert "prefix word2" in out, "清洗不得因去重替换崩溃"
    assert out.count("prefix word2") <= 2, "近重复段应被合并"


def test_full_pipeline_ordered():
    c = _clen()
    text = (
        "　机密-内部使用\n机密-内部使用\n机密-内部使用\n\n"
        "第 2 页\n\n"
        "员工手册规定年假申请需提前三个工作日通过OA系统提交并审批生效。\n\n"
        "员工手册规定年假申请需提前三个工作日通过OA系统提交并审批生效。\n\n"
        "短\n\n"
        "报销制度：出差交通住宿实报实销，普通费用审核通过后五个工作日打款至工资卡。"
    )
    out = c.clean(text)
    assert "机密" not in out, "重复页眉被去除"
    assert "第 2 页" not in out
    assert "短" not in out, "质量过滤剔除短段"
    assert out.count("员工手册规定") == 1, "重复段去重"
    assert "报销制度" in out
