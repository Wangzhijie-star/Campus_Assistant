# 校园 SQLBot 串行评测工具

**团队交接优先阅读 [团队测试SOP](团队测试SOP.md)**：包含环境配置、20题串行执行、手动或Codex评分、JSON校验、Excel导出依赖及GitHub提交范围。

以下内容为技术速查。旧 collect.py 单题流程保留，新工具通过 cli.py 使用。个人设计文档及历史操作记录不随工具发布。

## 当前边界

自动提交但严格串行；每题新会话、统一数据源；不自动重试问数。终态不明暂停，run 再次启动时先查询原请求。批末只读采集；评分可手动发送给AI或委托Codex离线完成，脚本未集成评分模型API。single20_001已完成20题运行、评分JSON保存及校验；Excel导出是独立步骤。

已选问题文件为 `项目学习文档/数据集和测试问题集/单任务问题表 (2).xlsx`。其中没有 S001，不自动改号。必须显式指定实际题号；以下 S002,S003 仅为两题示例，不代表已确定正式 20 题。

## 准备环境

将整个 tools/sqlbot_eval 目录放在 Ubuntu 项目对应路径；问题表和模板也需位于该项目。使用已有 backend/.venv/bin/python，需 httpx、openpyxl 和后端依赖。数据库设置仍由后端读取，密码不要写进本工具配置或报告。数据库短只读事务，连接超时 10 秒、语句超时 10 秒。

复制 config.example.json 为本地配置，填写实际 base_url（含 /api/v1 前缀）和 datasource_id。配置名称已写为“软件2404班及李思达老师所带班级学生成绩数据”；创建会话前会验证 ID/名称。示例超时是可调整初值，不是测得的最优值。评分总权重默认空，避免未经确认跨维度算总分。模板原有业务/执行公式保留；人工与裁定分不自动填写。

在终端交互式读取当前有效令牌，避免进入 shell 历史：

```bash
read -rsp 'SQLBot token: ' SQLBOT_TOKEN
export SQLBOT_TOKEN
```

## 命令（在项目根目录）

```bash
backend/.venv/bin/python tools/sqlbot_eval/cli.py prepare \
  --config tools/sqlbot_eval/config.local.json \
  --questions '项目学习文档/数据集和测试问题集/单任务问题表 (2).xlsx' \
  --ids S002,S003 --batch-id smoke_001 \
  --rubric tools/sqlbot_eval/templates/rubric_v1.txt \
  --template '项目学习文档/数据集和测试问题集/Text2SQL评分表模板.xlsx'

# 以下命令会实际调用模型；确认配置后才运行
backend/.venv/bin/python tools/sqlbot_eval/cli.py run --batch tools/sqlbot_eval/reports/smoke_001

# 独立重采，不会重新问数
backend/.venv/bin/python tools/sqlbot_eval/cli.py collect --batch tools/sqlbot_eval/reports/smoke_001

# 离线生成 prompts；将回复存为 scores/<qid>/<run_id>.json
backend/.venv/bin/python tools/sqlbot_eval/cli.py review --batch tools/sqlbot_eval/reports/smoke_001

# 校验回复并生成 export_plan.json
backend/.venv/bin/python tools/sqlbot_eval/cli.py export --batch tools/sqlbot_eval/reports/smoke_001
```

Excel 导出使用 Node 与 @oai/artifact-tool（复用原成绩单生成技术）。可在有此依赖的机器运行：

```bash
node tools/sqlbot_eval/export_scorecard.mjs \
  tools/sqlbot_eval/reports/smoke_001/export_plan.json \
  tools/sqlbot_eval/reports/smoke_001/scorecard.xlsx
```

也可在 cli.py export 后加 `--output-xlsx 新文件路径 --node node可执行文件`。SQLBOT_ARTIFACT_MODULE 可指定 artifact-tool 的入口文件绝对路径。跨机器移动批次时，export_plan 的模板路径需重新运行 export 更新。更新已有人工作业的成绩单时加 `--base-xlsx 原成绩单路径`，输出必须是另一个新文件。

补跑：`cli.py rerun --batch 批次目录 --question-id S002` 只创建新运行，之后用 run 启动；同题仍有未知/未运行记录时拒绝补跑。不能自动选成功一次替换首轮结果。

## 文件与恢复

cases/rubric/template 是批次快照；mapping 保存关联和状态；trials 保存证据；review_inputs/prompts 保存评分材料；scores 保存人工转存的 AI JSON。已生成 review 的证据不会静默覆盖，需要调整材料时使用独立新批次/材料版本。已有批次 prepare 拒绝覆盖。错误退出码为 2；出现暂停不代表后端已取消。

不将日志包含的敏感学生信息上传给任何外部模型；本工具只在本地生成文本，由用户决定评分模型和发送范围。

## 离线验证

```bash
backend/.venv/bin/python -m unittest discover -s tools/sqlbot_eval/tests -v
```

测试包含模拟 HTTP 流、断连/超时、恢复不重发、后端失败继续、读取真实问题表以及评分证据与范围验证，不调用实际模型。
