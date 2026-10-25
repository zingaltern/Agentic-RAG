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


def test_normalize_width_default_keeps_cjk_punct():
    """V5.2 默认：只做宽度归一（全角字母数字/表意空格），中文标点保持不变。"""
    c = _clen(dedup=False, quality=False)
    out = c.clean("ＡＢＣ１２３　年假申请？　公司制度：报销规则。")
    assert "ABC123" in out, "全角字母数字应转半角"
    assert "？" in out and "：" in out and "。" in out, "中文标点默认不得转半角"
    assert "\u3000" not in out


def test_normalize_punct_opt_in():
    """V5.2：中文标点→半角为独立开关（normalize_punct=True 时旧行为）。"""
    c = _clen(dedup=False, quality=False, normalize_punct=True)
    out = c.clean("年假申请？  \n\n公司制度：\u3000报销规则。")
    assert "?" in out and ":" in out and "." in out
    assert "？" not in out and "：" not in out


def test_noise_removes_page_numbers_and_rulers():
    c = _clen(dedup=False, quality=False, normalize_punct=True)
    text = "公司制度\n\n第 3 页\n\n年假规定如下。\n\n----\n\nPage 4 of 10\n\n报销规则如下。"
    out = c.clean(text)
    assert "第 3 页" not in out
    assert "Page 4 of 10" not in out
    assert "----" not in out
    assert "年假规定如下." in out, "normalize_punct 开启时全角句号归一为半角"


def test_noise_removes_repeated_header_lines():
    c = _clen(dedup=False, quality=False, normalize_punct=True)
    text = "机密文件-内部使用\n机密文件-内部使用\n机密文件-内部使用\n\n正文字段。"
    out = c.clean(text)
    assert "机密文件" not in out, "重复页眉行应被去除"
    assert "正文字段." in out, "normalize_punct 开启时全角句号归一为半角"


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


# -- V5.2：分段 / 按类型豁免 / 安全阀 ------------------------------------------------


def test_auto_segmentation_line_split_enables_quality():
    """V5.2：整篇无空行（PDF 式单 \\n）时按行分段，质量过滤才能剔除废话行。"""
    c = _clen(dedup=False, quality=True, min_chunk_chars=20)
    text = ("标题短行\n"
            "年度报销制度规定交通与住宿费用实报实销，普通费用审核通过后五个工作日打款。\n"
            "备注短行")
    out = c.clean(text)
    assert "年度报销制度" in out
    assert "备注短行" not in out, "单换行文档也应按行分段并过滤短行"


def test_dedup_works_on_single_newline_doc():
    """V5.2：无空行文档同样做段落去重（修复"整篇=一段"时 dedup 失效）。"""
    c = _clen(noise=False, quality=False, dedup=True)
    a = "员工手册规定年假申请需提前三个工作日通过OA系统提交并经部门负责人审批生效。"
    out = c.clean(f"{a}\n{a}\n{text_other()}")
    assert out.count("员工手册规定") == 1


def test_exempt_kind_skips_quality_and_dedup():
    """V5.2：代码/结构化类型豁免 quality/dedup，避免把代码行误杀。"""
    c = _clen(dedup=True, quality=True, exempt_kinds=("py",))
    code = "x = 1\ny = 2\nreturn x + y\n"
    body = "年度报销制度规定交通与住宿费用实报实销，普通费用审核通过后五个工作日打款。"
    out = c.clean(f"短\n\n{code}", kind="py")
    assert "短" in out, "豁免类型不应用质量过滤"
    out2 = c.clean(f"短\n\n{body}", kind="md")
    assert "短" not in out2, "非豁免类型仍按质量过滤"


def test_max_drop_ratio_skips_quality_filter():
    """V5.2 安全阀：质量过滤删除占比超限 → 整体跳过（防整篇被清空）。"""
    c = _clen(dedup=False, quality=True, min_chunk_chars=1000, max_drop_ratio=0.3)
    text = "年假制度规定员工满一年享受五天年假，需提前三个工作日申请审批。"
    out = c.clean(text)
    assert "年假制度" in out, "短于阈值的段落删除超限时应整体跳过"


def test_dedup_ceiling_skips_dedup():
    """V5.2 安全阀：段落数超过 dedup_max_paragraphs → 跳过去重（防 O(n²)）。"""
    c = _clen(noise=False, quality=False, dedup=True, dedup_max_paragraphs=2)
    a = "员工手册规定年假申请需提前三个工作日通过OA系统提交并经部门负责人审批生效。"
    out = c.clean(f"{a}\n\n{a}\n\n{text_other()}")
    assert out.count("员工手册规定") == 2, "超上限跳过去重（两条均保留）"
