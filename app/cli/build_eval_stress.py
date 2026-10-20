"""复杂难度分层验证集生成器（DEVELOPMENT.md 8.5.1，`make eval` 的 complex 底层数据源）。

针对「验证语料全是 <2KB 纯文本、题面过于直白」的盲区，生成多格式/带噪声/分层难度的
验证集，产物随仓库提交：
- 语料 tests/eval/sample_data/complex/（虚构企业"云帆科技"域，事实可核验）：
  PDF（pymupdf 生成，含画线表格与多页）/ DOCX（含真表格）/ XLSX / PPTX / HTML /
  EML 邮件线程 / 长文 Markdown（≥3000 字埋点）/ 噪声 TXT（错别字+OCR 空格+广告行）/ 
  近重复对与完全重复对（后者顺带验证入库幂等语义）；
- 测试对 tests/eval/qa_pairs/complex/qa_pairs.jsonl：type 沿用 8.3 四类，
  variant 为难度分层标签（direct/paraphrase/multi_doc/table/noisy/long_doc/cross_fmt/
  oov/no_invent），报告"特征分布"逐层出通过率。

确定性：全部内容为静态定义（不依赖随机数），同参数再生成逐字节一致；--seed 仅保留
接口兼容。纯用既有依赖（pymupdf/python-docx/openpyxl/python-pptx/标准库），零新增。
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import pymupdf
from docx import Document as _DocxDocument
from openpyxl import Workbook as _XlsxWorkbook
from pptx import Presentation as _PptxPresentation

DEFAULT_DOCS_DIR = Path("tests/eval/sample_data/complex")
DEFAULT_PAIRS = Path("tests/eval/qa_pairs/complex/qa_pairs.jsonl")

# -- 文件名常量（问题表引用，改名必须同步问题表） --------------------------------
PDF_TRAVEL = "云帆科技-差旅管理办法v3.pdf"
PDF_PROCURE = "云帆科技-服务器采购流程.pdf"
PDF_SEC = "云帆科技-信息安全红线.pdf"
PDF_ROADMAP = "云帆科技-2026年Q2产品路线图.pdf"
DOCX_HANDBOOK = "云帆科技-员工手册.docx"
DOCX_EXPENSE = "云帆科技-报销制度.docx"
DOCX_PM = "云帆科技-项目管理规范.docx"
DOCX_AFTER = "云帆科技-售后服务条款.docx"
XLSX_BUDGET = "云帆科技-2026年部门预算.xlsx"
XLSX_ASSET = "云帆科技-固定资产清单.xlsx"
XLSX_SALES = "云帆科技-季度销售数据.xlsx"
PPTX_LAUNCH = "云帆科技-新品发布会.pptx"
PPTX_ONBOARD = "云帆科技-入职培训.pptx"
HTML_NOTICE = "云帆科技-内网公告.html"
HTML_FAQ = "云帆科技-常见问题FAQ.html"
HTML_ROOM = "云帆科技-会议室预订说明.html"
HTML_VPN = "云帆科技-VPN配置指南.html"
HTML_CONTACT = "云帆科技-通讯录.html"
EML_MEETING = "云帆科技-会议时间变更.eml"
EML_DOWNTIME = "云帆科技-系统停机通知.eml"
EML_EXPENSE = "云帆科技-报销系统上线.eml"
EML_DRILL = "云帆科技-安全演练通知.eml"
EML_HIRING = "云帆科技-招聘进度同步.eml"
MD_DUTY = "云帆科技-值班手册.md"
MD_POSTMORTEM = "云帆科技-故障复盘模板.md"
MD_REVIEW = "云帆科技-代码评审规范.md"
MD_DICT = "云帆科技-数据字典.md"
MD_SLA = "云帆科技-SLA服务等级说明.md"
MD_TRAINING = "云帆科技-内部培训笔记.md"
MD_OSS = "云帆科技-开源贡献指南.md"
MD_ARCH = "云帆科技-架构演进白皮书.md"
TXT_ASSET = "云帆科技-设备台账.txt"
TXT_DUTYLOG = "云帆科技-值班记录.txt"
TXT_FEEDBACK = "云帆科技-客户反馈摘要.txt"
TXT_STOCK = "云帆科技-库存清单.txt"
TXT_REPAIR = "云帆科技-维修日志.txt"
TXT_MINUTES = "云帆科技-会议速记.txt"
TXT_TICKET = "云帆科技-工单导出.txt"
TXT_GATE = "云帆科技-公告存档.txt"
TXT_ATT_V1 = "云帆科技-考勤制度v1.txt"
TXT_ATT_V2 = "云帆科技-考勤制度v2.txt"
TXT_VISITOR = "云帆科技-访客参观须知.txt"
TXT_VISITOR_COPY = "云帆科技-访客参观须知-副本.txt"

# -- 格式构建器（产物必须是真实二进制：解析器有魔数校验） ------------------------

# 固定时间戳：二进制格式（PDF 元数据 / zip 条目与 core properties）会嵌入生成时刻，
# 不固定则再生成字节漂移、content_hash 漂移，破坏"可复现"承诺
_FIXED_DT = (2026, 1, 1, 0, 0, 0)
_FIXED_ISO = b"2026-01-01T00:00:00Z"


def _pin_core_props(data: bytes) -> bytes:
    """钉死 docProps/core.xml 的 created/modified 时间戳。

    openpyxl 在 save() 时用当前 UTC 时间覆盖 `wb.properties.modified`（实测：跨秒两次
    生成字节不一致），仅设置 wb.properties 无效；此处按生成时的固定时间回钉两处。
    python-docx/python-pptx 不做覆盖，同样回钉以统一口径。
    """
    data = re.sub(rb"(<dcterms:created[^>]*>)[^<]*(</dcterms:created>)",
                  rb"\g<1>" + _FIXED_ISO + rb"\g<2>", data, count=1)
    return re.sub(rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)",
                  rb"\g<1>" + _FIXED_ISO + rb"\g<2>", data, count=1)


def _rezip_fixed(payload: bytes, pin_core_props: bool = False) -> bytes:
    """重写 zip 条目时间戳为固定值（docx/xlsx/pptx 的 ZipFile 会写入当前时刻）。

    pin_core_props=True 时同时回钉 core.xml 时间戳（openpyxl 保存覆盖问题）。
    """
    src, out = io.BytesIO(payload), io.BytesIO()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            zi = zipfile.ZipInfo(info.filename, date_time=_FIXED_DT)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = info.external_attr
            content = zin.read(info.filename)
            if pin_core_props and info.filename == "docProps/core.xml":
                content = _pin_core_props(content)
            zout.writestr(zi, content)
    return out.getvalue()


_FIXED_ID = b"/ID[<00000000000000000000000000000000><00000000000000000000000000000000>]"
# pymupdf 的 /ID 两段可能是 hex（<...>）或字面串（(...)，含转义字节）——两种都要匹配，
# 否则随机 ID 泄漏导致同一内容跨次重生成字节漂移（实测 1/10 概率命中字面串形式）。
_PDF_ID_RE = re.compile(
    rb"/ID\s*\[\s*(?:<[0-9A-Fa-f]*>|\((?:[^()\\]|\\.)*\))\s*"
    rb"(?:<[0-9A-Fa-f]*>|\((?:[^()\\]|\\.)*\))\s*\]")


def _fix_pdf_id(payload: bytes) -> bytes:
    """PDF trailer 的 /ID 由 pymupdf 随机生成 → 固定化（保证字节级可复现）。"""
    return _PDF_ID_RE.sub(_FIXED_ID, payload, count=1)


def _pdf_bytes(pages: list[dict]) -> bytes:
    """pymupdf 生成 PDF（内置 CJK 字体 china-s）。每页 {"lines": [...], "table": (表头, 行) 可选}。
    表格用画线网格 + 逐单元格文本，get_text 必可抽出（find_tables 识别与否不影响回环）。"""
    doc = pymupdf.open()
    for spec in pages:
        page = doc.new_page()
        y = 72.0
        for line in spec.get("lines", []):
            page.insert_text(pymupdf.Point(72, y), line, fontname="china-s", fontsize=11)
            y += 17
        table = spec.get("table")
        if table:
            headers, rows = table
            y += 10
            col_n = len(headers)
            col_w = 440.0 / col_n
            row_h = 22.0
            all_rows = [headers, *rows]
            for r, row in enumerate(all_rows):
                for c, cell in enumerate(row):
                    page.insert_text(pymupdf.Point(76 + c * col_w, y + 15), str(cell),
                                     fontname="china-s", fontsize=10)
                y += row_h
            top = y - row_h * len(all_rows)
            for i in range(len(all_rows) + 1):          # 横线
                gy = top + i * row_h
                page.draw_line(pymupdf.Point(72, gy), pymupdf.Point(72 + 440, gy))
            for j in range(col_n + 1):                  # 竖线
                gx = 72 + j * col_w
                page.draw_line(pymupdf.Point(gx, top), pymupdf.Point(gx, y))
    doc.set_metadata({"producer": "build_eval_stress", "creator": "", "title": "",
                      "author": "", "subject": "", "keywords": "",
                      "creationDate": "D:20260101000000", "modDate": "D:20260101000000"})
    return _fix_pdf_id(doc.tobytes())


def _docx_bytes(title: str, paragraphs: list[str], table: tuple[list[str], list[list]] | None) -> bytes:
    """python-docx 生成 DOCX；表格用真实 add_table（解析器按 w:tbl 提取 [TABLE] 块）。"""
    doc = _DocxDocument()
    doc.add_heading(title, level=1)
    for p in paragraphs:
        doc.add_paragraph(p)
    if table:
        headers, rows = table
        t = doc.add_table(rows=1 + len(rows), cols=len(headers))
        for c, cell in enumerate(headers):
            t.rows[0].cells[c].text = str(cell)
        for r, row in enumerate(rows, start=1):
            for c, cell in enumerate(row):
                t.rows[r].cells[c].text = str(cell)
    cp = doc.core_properties
    cp.author = "云帆科技评测"
    cp.last_modified_by = "build_eval_stress"
    cp.created = cp.modified = datetime(2026, 1, 1)
    return _rezip_fixed(_save_to_bytes(doc.save), pin_core_props=True)


def _save_to_bytes(save) -> bytes:
    bio = io.BytesIO()
    save(bio)
    return bio.getvalue()


def _xlsx_bytes(sheet: str, header: list[str], rows: list[list]) -> bytes:
    """openpyxl 生成 XLSX（解析器逐行 tab 拼接为文本段）。"""
    wb = _XlsxWorkbook()
    ws = wb.active
    ws.title = sheet
    ws.append(header)
    for row in rows:
        ws.append(row)
    wb.properties.created = wb.properties.modified = datetime(2026, 1, 1)
    # openpyxl 保存时会覆盖 properties.modified → 回钉 core.xml（见 _pin_core_props）
    return _rezip_fixed(_save_to_bytes(wb.save), pin_core_props=True)


def _pptx_bytes(slides: list[tuple[str, list[str]]]) -> bytes:
    """python-pptx 生成 PPTX（标题 + 要点文本框）。"""
    prs = _PptxPresentation()
    for title, bullets in slides:
        layout = prs.slide_layouts[1]
        slide = prs.slides.add_slide(layout)
        slide.shapes.title.text = title
        frame = slide.placeholders[1].text_frame
        frame.text = bullets[0]
        for b in bullets[1:]:
            frame.add_paragraph().text = b
    cp = prs.core_properties
    cp.author = "云帆科技评测"
    cp.last_modified_by = "build_eval_stress"
    cp.created = cp.modified = datetime(2026, 1, 1)
    return _rezip_fixed(_save_to_bytes(prs.save), pin_core_props=True)


def _eml_bytes(subject: str, body: str) -> bytes:
    """标准库生成 EML（解析器取 Subject 与 text/plain 正文）。"""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "admin@yunfan.example"
    msg["To"] = "all-staff@yunfan.example"
    msg.set_content(body)
    return msg.as_bytes()


def _html_bytes(title: str, body_html: str) -> bytes:
    """标准库拼 HTML（解析器按 p/div/li/tr/h1-h6 分块扁平化）。"""
    return (f"<html><head><meta charset='utf-8'><title>{title}</title></head>"
            f"<body>{body_html}</body></html>").encode("utf-8")


# -- 噪声注入（静态替换，确定性；garbled 门只认 U+FFFD，本方案天然不触发） --------

_AD_LINES = ("【广告】云帆自营商城数码焕新季限时特惠", "———— 以下内容由导出系统自动追加 ————")
_BOILERPLATE = "本文件由办公系统自动导出，仅限内部使用，请勿外传。"


def _noisy(title: str, facts: list[str], filler: list[str]) -> str:
    """噪声 TXT：错别字（形近替换）+ OCR 式空格 + 广告行 + 样板重复。
    事实行（facts）逐字保留以保证可检索性，噪声只注入填充行。"""
    typo_map = {"设备": "设各", "记录": "己录", "系统": "糸统", "报告": "报吿", "完成": "完戍"}
    lines = [f"{title}（内部存档导出版）", ""]
    body: list[str] = []
    for i, s in enumerate(facts + filler):
        is_fact = i < len(facts)
        if not is_fact:                               # 噪声只注入填充行
            for old, new in typo_map.items():
                if old in s and len(body) % 2 == 0:   # 只对部分行替换，占比受控
                    s = s.replace(old, new, 1)
            if i % 3 == 1:                            # OCR 式空格
                s = s.replace("，", " ， ").replace("的", " 的", 1)
        body.append(s)
        if i % 2 == 0:
            body.append(_BOILERPLATE)                 # 样板重复
        if i % 4 == 2:
            body.append(_AD_LINES[i % 2])             # 广告行
    return ("\n".join(lines + body) + "\n").encode("utf-8")


# -- 语料定义（事实句必须逐字出现在文档中；问题表据此生成） -----------------------

@dataclass
class CorpusDoc:
    filename: str
    payload: bytes
    facts: list[str] = field(default_factory=list)    # 供回环自检断言的关键句


def build_corpus() -> list[CorpusDoc]:
    """全部语料（静态定义，无随机数）。事实句与 8.5.1 难度分层问题表一一对应。"""
    docs: list[CorpusDoc] = []

    f_travel2 = "机票改签费用需要部门总监审批"
    # PDF 表格行以空格拼接呈现：表格类事实按"单元格串"登记（逐字可抽出）
    docs.append(CorpusDoc(PDF_TRAVEL, _pdf_bytes([
        {"lines": ["云帆科技差旅管理办法 V3", "第一条 为规范差旅支出制定本办法，适用于全体员工。",
                   "第二条 出差城市按 一线/二类/三线 三档管理。"],
         "table": (["城市等级", "住宿标准（每晚）", "补贴（每天）"],
                   [["一线城市", "800 元", "150 元"], ["二类城市", "600 元", "100 元"],
                    ["三线城市", "450 元", "80 元"]])},
        {"lines": ["第三条 交通与审批", f"第四条 {f_travel2}。",
                   "第五条 本办法自 2026 年 4 月 1 日起施行，由行政管理部负责解释。"]},
    ]), [f_travel2, "一线城市 800 元", "二类城市 600 元", "三线城市 450 元"]))

    f_proc1 = "单笔金额 5 万元以下的服务器采购由部门经理审批"
    f_proc2 = "采购金额 20 万元及以上需 CTO 联签"
    docs.append(CorpusDoc(PDF_PROCURE, _pdf_bytes([
        {"lines": ["云帆科技服务器采购流程（2026 修订）", "为提升采购效率与合规性，明确各金额段审批权限如下表。"],
         "table": (["金额区间", "审批人", "到货时限"],
                   [["5 万元以下", "部门经理", "10 个工作日"], ["5 万至 20 万元", "采购委员会", "15 个工作日"],
                    ["20 万元及以上", "CTO 联签", "20 个工作日"]])},
        {"lines": ["流程说明：", f"1. {f_proc1}；", f"2. {f_proc2}。",
                   "3. 验收合格后由资产管理部登记固定资产编号。"]},
    ]), [f_proc1, f_proc2, "5 万至 20 万元 采购委员会"]))

    f_sec1 = "办公电脑密码每 90 天必须强制更换一次"
    f_sec2 = "生产数据导出必须经过双人复核"
    docs.append(CorpusDoc(PDF_SEC, _pdf_bytes([
        {"lines": ["云帆科技信息安全红线", f"1. {f_sec1}，禁止多账户复用同一密码。",
                   f"2. {f_sec2}，导出记录留存一年。",
                   "3. 离开工位必须锁屏，门禁卡严禁外借。",
                   "违反红线条款将触发安全问责流程，由信息安全部认定并通报。"]},
    ]), [f_sec1, f_sec2]))

    f_road1 = "星尘 X2 计划于二季度末发布"
    f_road2 = "星尘 X2 官方定价 3999 元"
    docs.append(CorpusDoc(PDF_ROADMAP, _pdf_bytes([
        {"lines": ["云帆科技 2026 年 Q2 产品路线图", "本路线图经产品委员会评审通过，未发布信息注意保密。"],
         "table": (["产品型号", "发布窗口", "定价", "目标人群"],
                   [["星尘 X2", "二季度末", "3999 元", "商务人群"], ["云帆 Pad5", "三季度初", "2599 元", "教育用户"],
                    ["星尘 Mini", "三季度末", "1999 元", "学生群体"]])},
        {"lines": [f"重点型号说明：{f_road1}，{f_road2}，首发赠一年碎屏保障。"]},
    ]), [f_road1, f_road2, "云帆 Pad5 三季度初", "星尘 Mini 三季度末"]))

    f_hand1 = "试用期最长不超过 6 个月"
    # 年假数字仅存在于表格中：按单元格串登记（table 难度层依赖此点）
    docs.append(CorpusDoc(DOCX_HANDBOOK, _docx_bytes(
        "云帆科技员工手册（节选）",
        ["欢迎加入云帆科技。本手册约定劳动合同、考勤与假期等基础人事政策。",
         f"劳动合同期限三个月以上不满一年的，{f_hand1}。",],
        (["司龄", "法定框架下年假天数", "补充说明"],
         [["不满 5 年", "10 天", "含试用期"], ["满 5 年", "15 天", "按自然年核定"], ["满 10 年", "20 天", "上限"]])) ,
        [f_hand1, "10 天", "15 天", "20 天"]))

    f_exp1 = "差旅报销需在费用发生后 10 个工作日内提交"
    f_exp2 = "业务招待费必须提前一个工作日完成审批"
    docs.append(CorpusDoc(DOCX_EXPENSE, _docx_bytes(
        "云帆科技报销制度",
        [f"1. {f_exp1}（T+10），逾期系统自动关闭单据。", f"2. {f_exp2}。",
         "3. 电子发票需查验真伪并绑定报销单号。"],
        (["报销类型", "提交时限", "备注"], [["差旅费", "T+10 工作日", "含住宿与交通"],
                                          ["办公采购", "T+5 工作日", "附验收单"],
                                          ["培训费", "T+15 工作日", "需结业证明"]])) ,
        [f_exp1, f_exp2]))

    f_pm1 = "每个迭代的固定周期为两周"
    docs.append(CorpusDoc(DOCX_PM, _docx_bytes(
        "云帆科技项目管理规范",
        [f"1. {f_pm1}，包含 9 个工作日开发与 1 个工作日缓冲。",
         "2. 需求变更必须走变更评审，评审会每周三下午举行。",
         "3. 迭代目标一经评审锁定，迭代内不允许插入新需求。"],
        None),
        [f_pm1, "评审会每周三下午举行"]))

    f_after2 = "整机质保期为 12 个月"
    docs.append(CorpusDoc(DOCX_AFTER, _docx_bytes(
        "云帆科技售后服务条款",
        [f"{f_after2}，自验收合格之日起算。", "故障分级与响应时限如下表。"],
        (["故障等级", "响应时限", "修复时限"], [["P1（业务中断）", "2 小时", "24 小时"],
                                              ["P2（功能受损）", "8 小时", "3 个工作日"],
                                              ["P3（体验问题）", "2 个工作日", "下个版本"]])) ,
        [f_after2, "P1（业务中断）", "8 小时", "3 个工作日"]))

    # XLSX 解析按行 tab 拼接单元格：facts 记录单元格级关键串（供回环断言），问题表仍用自然句
    docs.append(CorpusDoc(XLSX_BUDGET, _xlsx_bytes(
        "部门预算", ["部门", "年度预算（万元）", "负责人"],
        [["研发部", 1200, "李明"], ["市场部", 380, "赵倩"], ["人力资源部", 260, "孙楠"],
         ["行政管理部", 180, "周舟"]]),
        ["研发部\t1200\t李明", "市场部\t380\t赵倩"]))

    docs.append(CorpusDoc(XLSX_ASSET, _xlsx_bytes(
        "固定资产", ["资产编号", "类别", "持有人/位置", "入账日期"],
        [["ZC-1024", "笔记本电脑", "陈晓", "2026-01-12"], ["ZC-2048", "服务器", "A 机房", "2026-02-03"],
         ["ZC-3050", "打印机", "前台公用", "2025-11-20"]]),
        ["ZC-1024\t笔记本电脑\t陈晓", "ZC-2048\t服务器\tA 机房"]))

    docs.append(CorpusDoc(XLSX_SALES, _xlsx_bytes(
        "季度销售", ["季度", "销售额（万元）", "同比"], [["Q1", 4860, "+12%"], ["Q2", 5230, "+15%"],
                                                     ["Q3", 5680, "+18%"]]),
        ["Q1\t4860", "Q2\t5230", "Q3\t5680"]))

    f_launch1 = "星尘系列新品发布会定于 3 月 15 日举行"
    f_launch2 = "星尘 X2 搭载自研玄澈芯片"
    docs.append(CorpusDoc(PPTX_LAUNCH, _pptx_bytes([
        ("星尘系列新品发布会方案", [f_launch1, "地点：科技园 B 座发布厅，同步线上直播", "议程：高管致辞 / 新品揭幕 / 媒体问答"]),
        ("星尘 X2 核心卖点", [f_launch2, "首发价 3999 元，赠一年碎屏保障", "渠道：官网商城与授权门店同步开售"]),
    ]), [f_launch1, f_launch2]))

    f_onboard1 = "新员工入职培训为期 3 天"
    f_onboard2 = "导师制辅导期为 6 个月"
    docs.append(CorpusDoc(PPTX_ONBOARD, _pptx_bytes([
        ("入职培训安排", [f_onboard1, "第一天文化与制度，第二天产品与技术，第三天团队实践"]),
        ("导师制说明", [f_onboard2, "导师每月与新人至少一次一对一沟通"]),
    ]), [f_onboard1, f_onboard2]))

    f_notice1 = "公司总部搬迁至科技园 B 座 12 层"
    docs.append(CorpusDoc(HTML_NOTICE, _html_bytes("内网公告",
        f"<h1>内网公告</h1><p>{f_notice1}，搬迁周末完成，周一正常办公。</p>"
        "<p>工位分布图见行政部邮件，访客接待同步移至新址前台。</p>"),
        [f_notice1]))

    f_faq1 = "工卡补办费用为 50 元"
    f_faq2 = "VPN 密码重置入口在门户自助服务页"
    docs.append(CorpusDoc(HTML_FAQ, _html_bytes("常见问题 FAQ",
        f"<h1>员工常见问题 FAQ</h1><ul><li>{f_faq1}，从工资代扣。</li>"
        f"<li>{f_faq2}，重置后 10 分钟内生效。</li>"
        "<li>社保基数每年 7 月调整一次。</li></ul>"),
        [f_faq1, f_faq2]))

    f_room1 = "单间会议室一次最长可预订 4 小时"
    docs.append(CorpusDoc(HTML_ROOM, _html_bytes("会议室预订说明",
        f"<h1>会议室预订说明</h1><p>{f_room1}，取消请提前 1 小时操作。</p>"
        "<p>发布厅与培训教室需行政部人工审批。</p>"),
        [f_room1]))

    f_vpn1 = "VPN 网关连接端口为 443"
    docs.append(CorpusDoc(HTML_VPN, _html_bytes("VPN 配置指南",
        f"<h1>VPN 配置指南</h1><p>服务器地址 vpn.yunfan.example，{f_vpn1}，认证方式为账号加动态口令。</p>"
        "<p>遇连接失败请先切换热点重试，再联系 IT 服务台。</p>"),
        [f_vpn1]))

    f_contact1 = "IT 服务台分机 8888"
    f_contact2 = "行政前台分机 8001"
    docs.append(CorpusDoc(HTML_CONTACT, _html_bytes("公司通讯录（常用分机）",
        "<h1>常用分机通讯录</h1>"
        "<table><tr><td>部门</td><td>分机</td></tr>"
        "<tr><td>IT 服务台</td><td>8888</td></tr>"
        "<tr><td>行政前台</td><td>8001</td></tr></table>"
        "<p>速记：IT 服务台分机 8888，行政前台分机 8001。</p>"),
        [f_contact1, f_contact2]))

    f_meet1 = "本周产品评审会由周四改到周五上午十点"
    docs.append(CorpusDoc(EML_MEETING, _eml_bytes("【变更】产品评审会时间调整",
        f"各位同事：{f_meet1}，地点不变（B 座 3-05 会议室）。"
        "\n请提前更新日程，材料仍按原清单准备。\n\n——项目管理部"),
        [f_meet1]))

    f_down1 = "核心系统将于本周六 22 点至 24 点停机维护"
    docs.append(CorpusDoc(EML_DOWNTIME, _eml_bytes("【通知】核心系统停机维护窗口",
        f"各位同事：{f_down1}，期间工单系统不可用。"
        "\n值班同学请按应急预案处理告警。\n\n——运维部"),
        [f_down1]))

    f_expsys1 = "新报销系统将于 4 月 1 日上线"
    docs.append(CorpusDoc(EML_EXPENSE, _eml_bytes("【上线】新报销系统切换通知",
        f"各位同事：{f_expsys1}，旧系统同日只读。"
        "\n迁移期间历史单据可在新系统查询。\n\n——财务部"),
        [f_expsys1]))

    f_drill1 = "公司将于 5 月 20 日开展全员钓鱼邮件演练"
    docs.append(CorpusDoc(EML_DRILL, _eml_bytes("【预告】安全演练安排",
        f"各位同事：{f_drill1}，点击模拟钓鱼链接会被记录并计入部门安全分。\n\n——信息安全部"),
        [f_drill1]))

    f_hire1 = "后端开发岗位本季度已发放 3 份 offer"
    docs.append(CorpusDoc(EML_HIRING, _eml_bytes("【同步】研发招聘进度",
        f"各位负责人：{f_hire1}，到面 5 人，流程继续推进。\n\n——人力资源部"),
        [f_hire1]))

    f_duty1 = "值班工程师接到告警后须在 15 分钟内响应"
    docs.append(CorpusDoc(MD_DUTY, _md_doc("云帆科技值班手册",
        ["# 云帆科技值班手册", "", f"## 响应要求", "", f"{f_duty1}，超时升级到值班经理。",
         "", "## 交接班", "", "交接班须书面确认未闭环工单与在途变更，重大事项当面口头交接。"]),
        [f_duty1]))

    f_post1 = "故障复盘须在故障关闭后 5 个工作日内完成"
    docs.append(CorpusDoc(MD_POSTMORTEM, _md_doc("云帆科技故障复盘模板",
        ["# 云帆科技故障复盘模板", "", f"## 时限", "", f"{f_post1}，输出物归档至知识库复盘专区。",
         "", "## 必答项", "", "时间线 / 根因 / 影响面 / 改进项（含责任人与截止日期）。"]),
        [f_post1]))

    f_cr1 = "代码合并至少需要 1 名高级工程师评审通过"
    docs.append(CorpusDoc(MD_REVIEW, _md_doc("云帆科技代码评审规范",
        ["# 云帆科技代码评审规范", "", f"## 评审门槛", "", f"{f_cr1}，核心模块需要 2 名。",
         "", "## 时限", "", "评审请求发出后 1 个工作日内必须给出首轮意见。"]),
        [f_cr1]))

    f_dict1 = "用户表 user_id 字段长度为 64"
    docs.append(CorpusDoc(MD_DICT, _md_doc("云帆科技数据字典（节选）",
        ["# 云帆科技数据字典（节选）", "", "## 用户表 user", "", f"user_id：主键，{f_dict1}，全局唯一。",
         "", "## 工单表 ticket", "", "ticket_no：业务单号，长度 32，带前缀 GD。"]),
        [f_dict1, "长度 32，带前缀 GD"]))

    f_sla1 = "云帆科技核心服务可用性承诺为 99.9%"
    docs.append(CorpusDoc(MD_SLA, _md_doc("云帆科技 SLA 服务等级说明",
        ["# 云帆科技 SLA 服务等级说明", "", f"## 可用性", "", f"{f_sla1}，按月度核算，未达标按合同赔付。",
         "", "## 赔付", "", "每低 0.1 个百分点赔付当月服务费的 5%，上限 30%。"]),
        [f_sla1]))

    f_train1 = "公司内训课程每月举办一次"
    docs.append(CorpusDoc(MD_TRAINING, _md_doc("云帆科技内部培训笔记",
        ["# 云帆科技内部培训笔记", "", f"## 节奏", "", f"{f_train1}，主题由技术委员会轮值出题。",
         "", "## 记录", "", "培训录像与讲义在门户知识库留存一年。"]),
        [f_train1]))

    f_oss1 = "开源贡献的 PR 必须通过 CI 后才能请求合并"
    docs.append(CorpusDoc(MD_OSS, _md_doc("云帆科技开源贡献指南",
        ["# 云帆科技开源贡献指南", "", f"## 合并门槛", "", f"{f_oss1}，且需 1 名 maintainer 批准。",
         "", "## 许可", "", "对外开源组件统一使用 Apache-2.0 许可证。"]),
        [f_oss1]))

    # 长文（≥3000 字，事实埋点在中后部）
    arch_fillers = [
        "云帆科技的架构演进始终围绕业务增长与稳定性两条主线展开，坚持小步快跑与灰度验证。",
        "早期单体应用支撑了从零到一的验证期，随着团队扩张，模块边界与发布效率成为主要矛盾。",
        "服务化改造按照业务能力垂直拆分，先从流量较小的非核心域试点，再逐步覆盖交易主链路。",
        "注册中心与配置中心采用双机房部署，任何一个机房故障都不会影响服务发现与配置下发。",
        "可观测体系基于统一接入的日志、指标与链路追踪三支柱，告警收敛策略持续迭代。",
        "数据层引入分库分表与读写分离，热点账户单独治理，账务对账链路保持幂等可重放。",
        "发布系统支持原地重启与蓝绿两种模式，回滚脚本与演练纳入每个季度例行检查。",
        "容量规划每半年评审一次，大促前按峰值倍数进行全链路压测并输出扩容清单。",
        "安全体系从边界防护延伸到纵深防御，密钥管理与审计日志由独立团队负责运营。",
        "成本治理将资源利用率纳入部门考核，闲置资源自动回收，冷数据分层归档。",
    ]
    f_arch1 = "自研统一接入网关命名为天枢"
    f_arch2 = "目标将核心链路 P99 延迟降至 150 毫秒"
    f_arch3 = "核心链路迁移计划在 2026 年内完成"
    f_arch4 = "单元化改造按流量分三阶段推进"
    f_arch5 = "服务网格选用自研轻量 sidecar 方案"
    f_arch6 = "异地多活的容灾目标为 RPO 小于 30 秒"
    arch_body = "## 一、演进背景\n\n" + "。\n\n".join(arch_fillers[:5]) + "。\n\n" + \
        "## 二、服务化与网关\n\n" + "。\n\n".join(arch_fillers[5:8]) + "。\n\n" + \
        f"进入网关统一阶段后，{f_arch1}，路由、鉴权与限流策略全部收敛到网关层。\n\n" + \
        f"性能专项同步启动，{f_arch2}，手段包括连接池治理与序列化优化。\n\n" + \
        "## 三、多活与单元化\n\n" + "。\n\n".join(arch_fillers[8:]) + "。\n\n" + \
        f"{f_arch4}，先接入只读流量，再迁移核心写流量，最后全量切流。\n\n" + \
        f"多活建设方面，{f_arch6}，数据层双向同步并定期演练机房切换。\n\n" + \
        f"## 四、2026 落地计划\n\n按季度排期，{f_arch3}，季度末进行切换演练与评审。\n\n" + \
        f"网格化阶段，{f_arch5}，避免引入重量级控制面带来的运维负担。\n\n" + \
        "## 五、风险与回滚\n\n每一阶段都保留回滚预案，切流窗口选择业务低峰，重大操作双人复核。\n\n" + \
        "本白皮书由架构组维护，评审通过后作为未来两年架构演进的基线文档。\n"
    arch_text = "# 云帆科技架构演进白皮书\n\n" + arch_body
    while len(arch_text) < 3200:                       # 确保长文体量（静态追加叙述）
        arch_text += "架构演进是一项长期工程，需要在稳定性、成本与效率之间持续权衡。\n"
    docs.append(CorpusDoc(MD_ARCH, arch_text.encode("utf-8"),
                          [f_arch1, f_arch2, f_arch3, f_arch4, f_arch5, f_arch6]))

    # 噪声 TXT（错别字/OCR 空格/广告行/样板重复）
    f_nasset = "台账编号 SB-301 为网络打印机"
    docs.append(CorpusDoc(TXT_ASSET, _noisy("设备台账（办公区）",
        [f_nasset, "台账编号 SB-302 为碎纸机，已报废。"],
        ["台账每月末盘点一次。", "新增设备入库需扫描二维码登记。"]), [f_nasset]))

    f_nduty = "6 月 3 日凌晨报告硬盘阵列告警"
    docs.append(CorpusDoc(TXT_DUTYLOG, _noisy("值班记录（运维）",
        [f_nduty, "现场确认后更换硬盘并重建阵列。"],
        ["当班工程师：刘洋。", "交接事项：观察阵列重建进度。"]), [f_nduty]))

    f_nfeed = "本期客户满意度调查综合得分 92 分"
    docs.append(CorpusDoc(TXT_FEEDBACK, _noisy("客户反馈摘要（客服）",
        [f_nfeed, "差评集中在物流时效。"],
        ["样本量 1200 份。", "已转交履约组跟进。"]), [f_nfeed]))

    f_nstock = "显示器当前库存 45 台"
    docs.append(CorpusDoc(TXT_STOCK, _noisy("库存清单（行政）",
        [f_nstock, "键盘库存 120 个。"],
        ["低于安全库存 50 台需补货。", "采购联系人：周舟。"]), [f_nstock]))

    f_nrepair = "会议室空调维修耗时 2 小时"
    docs.append(CorpusDoc(TXT_REPAIR, _noisy("维修日志（物业）",
        [f_nrepair, "更换电容后恢复正常。"],
        ["报修渠道：物业小程序。", "回访结果：满意。"]), [f_nrepair]))

    f_nminutes = "会议速记明确下季度聚焦三个目标"
    docs.append(CorpusDoc(TXT_MINUTES, _noisy("会议速记（管理层周会）",
        [f_nminutes, "降本增效、渠道拓展、产品打磨。"],
        ["下次周会时间待通知。", "纪要整理：孙楠。"]), [f_nminutes]))

    f_nticket = "工单 GD-889 已关闭"
    docs.append(CorpusDoc(TXT_TICKET, _noisy("工单导出（服务台）",
        [f_nticket, "处理人：IT 服务台。"],
        ["导出时间：本月 1 日。", "字段说明见数据字典。"]), [f_nticket]))

    f_ngate = "门禁系统升级安排在本周末进行"
    docs.append(CorpusDoc(TXT_GATE, _noisy("公告存档（行政）",
        [f_ngate, "升级期间请走访客通道。"],
        ["带来不便敬请谅解。", "咨询电话：分机 8001。"]), [f_ngate]))

    # 近重复对：v1/v2 只差打卡窗口（检索需区分版本）
    f_att1 = "弹性打卡窗口为 30 分钟"
    f_att2 = "弹性打卡窗口放宽为 60 分钟"
    f_att3 = "考勤异常需在 3 个工作日内申诉"
    v1_text = ("云帆科技考勤制度 V1\n\n1. 工作日 9:00 起算工时，" + f_att1 + "。\n"
               "2. 每月漏卡 3 次以内不作处理。\n"
               "3. " + f_att3 + "，逾期视为放弃。\n")
    v2_text = v1_text.replace(f_att1, f_att2).replace("V1", "V2（2026 修订）")
    docs.append(CorpusDoc(TXT_ATT_V1, v1_text.encode("utf-8"), [f_att1]))
    docs.append(CorpusDoc(TXT_ATT_V2, v2_text.encode("utf-8"), [f_att2, f_att3]))

    # 完全重复对：字节级相同（顺带验证入库幂等：副本不会产生新文档）
    visitor_text = ("云帆科技访客参观须知\n\n1. 访客须提前 1 个工作日预约登记。\n"
                    "2. 参观期间须由接待人全程陪同。\n3. 禁止在未授权区域拍照。\n")
    docs.append(CorpusDoc(TXT_VISITOR, visitor_text.encode("utf-8"), ["访客须提前 1 个工作日预约登记"]))
    docs.append(CorpusDoc(TXT_VISITOR_COPY, visitor_text.encode("utf-8"), []))

    return docs


def _md_doc(title: str, lines: list[str]) -> bytes:
    return "\n".join(lines).encode("utf-8")


# -- 问题表（90 条：variant × type 分层；expected 与语料强一致） -------------------

def build_pairs() -> list[dict]:
    """难度分层测试对。direct/table/noisy/long_doc 期望单文档；multi_doc/cross_fmt 期望两文档。"""
    q: list[dict] = []

    def add(variant: str, type_: str, query: str, expected: list[str] | None = None):
        q.append({"variant": variant, "type": type_, "query": query, "expected": expected})

    # direct ×24（单文档事实，实体明确）
    add("direct", "direct", "出差住宿每晚最高可以报销多少钱？", [PDF_TRAVEL])
    add("direct", "direct", "机票改签费用需要谁审批？", [PDF_TRAVEL])
    add("direct", "direct", "单笔服务器采购金额 5 万元以下由谁审批？", [PDF_PROCURE])
    add("direct", "direct", "办公电脑密码多少天必须更换一次？", [PDF_SEC])
    add("direct", "direct", "星尘 X2 的官方定价是多少元？", [PDF_ROADMAP])
    add("direct", "direct", "试用期最长不超过几个月？", [DOCX_HANDBOOK])
    add("direct", "direct", "差旅报销需要在费用发生后多少个工作日内提交？", [DOCX_EXPENSE])
    add("direct", "direct", "一个迭代的固定周期是多久？", [DOCX_PM])
    add("direct", "direct", "P1 级故障的响应时限是多长？", [DOCX_AFTER])
    add("direct", "direct", "研发部 2026 年的年度预算是多少万元？", [XLSX_BUDGET])
    add("direct", "direct", "资产编号 ZC-1024 是什么设备？持有人是谁？", [XLSX_ASSET])
    add("direct", "direct", "2026 年 Q2 的销售额是多少万元？", [XLSX_SALES])
    add("direct", "direct", "星尘系列新品发布会定在哪一天？", [PPTX_LAUNCH])
    add("direct", "direct", "新员工入职培训为期几天？", [PPTX_ONBOARD])
    add("direct", "direct", "公司总部搬迁到了哪里？", [HTML_NOTICE])
    add("direct", "direct", "工卡补办需要多少费用？", [HTML_FAQ])
    add("direct", "direct", "VPN 网关的连接端口是多少？", [HTML_VPN])
    add("direct", "direct", "产品评审会改到什么时间？", [EML_MEETING])
    add("direct", "direct", "核心系统停机维护安排在什么时间？", [EML_DOWNTIME])
    add("direct", "direct", "新报销系统什么时候上线？", [EML_EXPENSE])
    add("direct", "direct", "值班工程师接到告警后多少分钟内必须响应？", [MD_DUTY])
    add("direct", "direct", "代码合并至少需要几名高级工程师评审通过？", [MD_REVIEW])
    add("direct", "direct", "用户表 user_id 字段的长度是多少？", [MD_DICT])
    add("direct", "direct", "核心服务的可用性承诺是多少？", [MD_SLA])

    # paraphrase ×12（口语化/错字，type=rewrite）
    add("paraphrase", "rewrite", "出差住酒店一晚上最多能报多少？", [PDF_TRAVEL])
    add("paraphrase", "rewrite", "电脑密码多久得换一次啊", [PDF_SEC])
    add("paraphrase", "rewrite", "试用期最多多久就能转正呀", [DOCX_HANDBOOK])
    add("paraphrase", "rewrite", "报销单最晚啥时候要交？", [DOCX_EXPENSE])
    add("paraphrase", "rewrite", "服务器挂了多久得有人响应？", [DOCX_AFTER])
    add("paraphrase", "rewrite", "研发部今年预算有多少钱", [XLSX_BUDGET])
    add("paraphrase", "rewrite", "工卡丢了补一张多少钱", [HTML_FAQ])
    add("paraphrase", "rewrite", "会议室一次最多能订几个小时", [HTML_ROOM])
    add("paraphrase", "rewrite", "评审会是不是改时间了？改成啥时候了", [EML_MEETING])
    add("paraphrase", "rewrite", "vpn连接用哪个端口", [HTML_VPN])
    add("paraphrase", "rewrite", "值班告警多快之内要回复？", [MD_DUTY])
    add("paraphrase", "rewrite", "公司新地址在哪儿啊", [HTML_NOTICE])

    # multi_doc ×10（跨 2 文档综合）
    add("multi_doc", "direct", "一线城市住宿标准和 P1 故障响应时限分别是多少？", [PDF_TRAVEL, DOCX_AFTER])
    add("multi_doc", "direct", "服务器采购 20 万以上需要谁联签？生产数据导出有什么要求？", [PDF_PROCURE, PDF_SEC])
    add("multi_doc", "direct", "研发部年度预算和 2026 年 Q2 销售额分别是多少？", [XLSX_BUDGET, XLSX_SALES])
    add("multi_doc", "direct", "新品发布会日期和星尘 X2 定价分别是多少？", [PPTX_LAUNCH, PDF_ROADMAP])
    add("multi_doc", "direct", "差旅报销提交时限和新报销系统上线日期分别是？", [DOCX_EXPENSE, EML_EXPENSE])
    add("multi_doc", "direct", "IT 服务台分机和 VPN 端口分别是多少？", [HTML_CONTACT, HTML_VPN])
    add("multi_doc", "direct", "入职培训为期几天？工卡补办费用是多少？", [PPTX_ONBOARD, HTML_FAQ])
    add("multi_doc", "direct", "服务可用性承诺和 user_id 字段长度分别是？", [MD_SLA, MD_DICT])
    add("multi_doc", "direct", "会议室最长预订时长和访客预约要求分别是？", [HTML_ROOM, TXT_VISITOR])
    add("multi_doc", "direct", "现行弹性打卡窗口是多久？差旅住宿上限是多少？", [TXT_ATT_V2, PDF_TRAVEL])

    # table ×10（答案仅在表格里）
    add("table", "direct", "二类城市的住宿报销标准是每晚多少钱？", [PDF_TRAVEL])
    add("table", "direct", "三线城市的住宿标准和补贴分别是多少？", [PDF_TRAVEL])
    add("table", "direct", "采购金额 5 万到 20 万之间由谁审批？", [PDF_PROCURE])
    add("table", "direct", "星尘 X2 计划在哪个窗口发布？", [PDF_ROADMAP])
    add("table", "direct", "司龄满 10 年的员工年假是多少天？", [DOCX_HANDBOOK])
    add("table", "direct", "市场部 2026 年的年度预算是多少万元？", [XLSX_BUDGET])
    add("table", "direct", "资产编号 ZC-2048 是什么设备？放在哪里？", [XLSX_ASSET])
    add("table", "direct", "2026 年 Q3 的销售额是多少万元？", [XLSX_SALES])
    add("table", "direct", "P2 级故障的响应时限是多长？", [DOCX_AFTER])
    add("table", "direct", "IT 服务台的分机号是多少？", [HTML_CONTACT])

    # noisy ×8（噪声文档中的事实）
    add("noisy", "direct", "台账编号 SB-301 是什么设备？", [TXT_ASSET])
    add("noisy", "direct", "6 月 3 日的值班记录报告了什么告警？", [TXT_DUTYLOG])
    add("noisy", "direct", "本期客户满意度调查综合得分是多少？", [TXT_FEEDBACK])
    add("noisy", "direct", "显示器当前库存是多少台？", [TXT_STOCK])
    add("noisy", "direct", "会议室空调维修花了多长时间？", [TXT_REPAIR])
    add("noisy", "direct", "工单 GD-889 目前是什么状态？", [TXT_TICKET])
    add("noisy", "direct", "门禁系统升级安排在什么时间？", [TXT_GATE])
    add("noisy", "direct", "会议速记明确下季度聚焦几个目标？", [TXT_MINUTES])

    # long_doc ×6（长文埋点事实）
    add("long_doc", "direct", "架构白皮书里的自研统一接入网关叫什么名字？", [MD_ARCH])
    add("long_doc", "direct", "架构白皮书设定的核心链路 P99 延迟目标是多少毫秒？", [MD_ARCH])
    add("long_doc", "direct", "核心链路迁移计划在哪一年完成？", [MD_ARCH])
    add("long_doc", "direct", "单元化改造按流量分几个阶段推进？", [MD_ARCH])
    add("long_doc", "direct", "服务网格选用了什么方案？", [MD_ARCH])
    add("long_doc", "direct", "异地多活的容灾目标 RPO 是多少秒？", [MD_ARCH])

    # cross_fmt ×6（同一事实族分布在不同格式文档）
    add("cross_fmt", "direct", "星尘 X2 的定价、发布窗口和发布会日期分别是？", [PDF_ROADMAP, PPTX_LAUNCH])
    add("cross_fmt", "direct", "差旅住宿标准和员工手册里的年假上限分别是？", [PDF_TRAVEL, DOCX_HANDBOOK])
    add("cross_fmt", "direct", "质保期 12 个月和整机发布定价分别在哪些文件约定？", [DOCX_AFTER, PDF_ROADMAP])
    add("cross_fmt", "direct", "工卡补办费用和门禁系统升级时间分别在哪些文件里？", [HTML_FAQ, TXT_GATE])
    add("cross_fmt", "direct", "值班响应要求和故障复盘时限分别是多少？", [MD_DUTY, MD_POSTMORTEM])
    add("cross_fmt", "direct", "报销提交时限和迭代周期分别是多少？", [DOCX_EXPENSE, DOCX_PM])

    # oov ×8（域外，期望诚实兜底）
    for query in ["云帆科技 2027 年的营收目标是多少？", "公司食堂的午餐价格是多少？",
                  "云帆科技的海外分部在哪个城市？", "首席执行官的助理叫什么名字？",
                  "今年双十一的促销预算是多少？", "公司通勤班车有几条线路？",
                  "云帆科技的主要竞争对手有哪些？", "员工年度体检机构是哪家？"]:
        add("oov", "no_answer", query)

    # no_invent ×6（域内相关但语料无据）
    for query in ["云帆科技上一季度的净利润是多少？", "星尘 X2 的实际销量是多少台？",
                  "研发部今年实际招聘了多少人？", "公司年会定在哪一天？",
                  "云帆科技最新取得的专利编号是什么？", "服务器机房的具体经纬度是多少？"]:
        add("no_invent", "no_invent", query)

    # 组装 jsonl（direct 类排前，oov/no_invent 排后，id 顺序编号）
    out: list[dict] = []
    for i, item in enumerate(q, start=1):
        row = {"id": f"C{i:03d}", "query": item["query"], "type": item["type"],
               "variant": item["variant"]}
        if item["expected"]:
            row["expected_sources"] = item["expected"]
        out.append(row)
    return out


# -- 落盘与入口 ------------------------------------------------------------------

def write_outputs(docs: list[CorpusDoc], pairs: list[dict],
                  docs_dir: Path, pairs_path: Path) -> None:
    """落盘：先清理旧语料再写文档与 jsonl（重生成不留脏文件）。"""
    docs_dir.mkdir(parents=True, exist_ok=True)
    for old in docs_dir.iterdir():
        if old.is_file():
            old.unlink()
    for d in docs:
        (docs_dir / d.filename).write_bytes(d.payload)
    pairs_path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(p, ensure_ascii=False) for p in pairs)
    pairs_path.write_text(payload + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="复杂难度分层验证集生成器（DEVELOPMENT.md 8.5.1）")
    parser.add_argument("--out-docs", default=str(DEFAULT_DOCS_DIR), help="语料输出目录")
    parser.add_argument("--out-pairs", default=str(DEFAULT_PAIRS), help="qa_pairs.jsonl 输出路径")
    parser.add_argument("--seed", type=int, default=42,
                        help="随机种子（内容为静态定义，seed 不影响产物，仅保留接口兼容）")
    args = parser.parse_args()

    docs = build_corpus()
    pairs = build_pairs()
    write_outputs(docs, pairs, Path(args.out_docs), Path(args.out_pairs))

    by_fmt: dict[str, int] = {}
    for d in docs:
        by_fmt[Path(d.filename).suffix.lstrip(".")] = by_fmt.get(Path(d.filename).suffix.lstrip("."), 0) + 1
    by_variant: dict[str, int] = {}
    for p in pairs:
        by_variant[p["variant"]] = by_variant.get(p["variant"], 0) + 1
    fmt_stat = "，".join(f"{k}×{v}" for k, v in sorted(by_fmt.items()))
    var_stat = "，".join(f"{k}×{v}" for k, v in by_variant.items())
    print(f"语料 {len(docs)} 份（{fmt_stat}）→ {args.out_docs}")
    print(f"测试对 {len(pairs)} 条（{var_stat}）→ {args.out_pairs}")
    print(f"评测随 make eval（--all-kbs）自动纳入 complex 库 [{time.strftime('%Y-%m-%d %H:%M:%S')}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
