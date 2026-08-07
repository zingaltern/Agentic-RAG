# tests/eval — 评测集与评测工具

## 目录结构

```
tests/eval/
├── README.md           # 本文件
├── qa_pairs.json       # 评测 QA 集（21 条示例）
├── rag_eval.py         # 最小评测脚本（纯标准库，API 可用后运行）
└── sample_data/        # 配套 Demo 文档（3 份）
    ├── 员工手册.md
    ├── IT支持手册.md
    └── 产品FAQ.txt
```

## 1. 如何使用（在 API 开发完成后）

```bash
# 启动服务（另开终端）：make run  →  http://localhost:8000

# 先入库 demo 文档（上传 /ui 或直接调 POST /api/documents/upload）
# 再运行评测：
python tests/eval/rag_eval.py \
  --pairs tests/eval/qa_pairs.json \
  --base-url http://localhost:8000 \
  --out reports/eval-demo.md
```

脚本会逐条调用 `POST /api/query`，输出 Markdown 报告，含：
- **Hit@5**：`expected_sources` 是否出现在返回 `sources` 中（direct/rewrite 类）
- **兜底率**：`no_answer` 类是否按预期返回 `code=21001`
- **防幻觉**：`no_invent` 类是否出现"编造"（回答含无据信息或未提示知识库无该信息）
- 每类命中明细，方便定位检索/改写问题

## 2. QA 类型说明（对应 DEVELOPMENT.md 8.3 指标）

| type | 含义 | 预期行为 | 指标 |
|---|---|---|---|
| `direct` | 直接命中知识库 | `answer_found` | Hit@5、Context Recall |
| `rewrite` | 含口语/乱序/同义词，需触发查询改写（反思循环） | `answer_found` | 反思迭代数分布 |
| `no_answer` | 知识库确实没有 | `fallback_21001`（兜底文案） | 兜底率 |
| `no_invent` | 关键词能搜到，但答案在文中无依据 | 拒答或明确"未找到依据"（不得编造） | Faithfulness |

## 3. 怎么换成你们真实的业务数据集

1. 上传你们的真实文档（docx/pdf/md/txt 均可），观察 `/ui` 文档列表的 `metadata.source` 值（就是文件名）；
2. 把 `qa_pairs.json` 整体替换，字段契约保持不变：
   ```json
   { "id": "Q01", "type": "direct",
     "query": "……",
     "expected_sources": ["<上传时的文件名>"],
     "golden_answer": "……",
     "expected_behavior": "answer_found" }
   ```
3. 建议每类比例：direct ≥ 60%、rewrite 15%、no_answer 15%、no_invent 10%；总量至少 20 条；
4. 新问题用真实用户提问语料（如工单、客服记录）收集，别自己编题。

## 4. 注意

- demo 文档的 `metadata.source` 必须与 `expected_sources` 文件名**逐字一致**（含扩展名、大小写）。
- 评测脚本不会替你做"改写是否生效"的因果判定：rewrite 类请人工抽查 `debug_trace` 迭代过程。
- 接入真实数据后请删除 `sample_data/`（或挪出仓库），避免把 demo 文档误传为正式知识库。