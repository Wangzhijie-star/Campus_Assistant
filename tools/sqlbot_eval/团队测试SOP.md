# 校园 SQLBot 测试 SOP（团队版）

目标：串行提交20道单任务问题 → 采集过程与结果 → 生成评分提示词 → AI评分 → 校验JSON → 写入Excel。每题创建独立会话，使用同一个数据源。不要把运行失败的题删除，它也是本轮结果。

## 1. 工具在哪里

- 当前实际运行目录：Ubuntu `/home/sqlbotdev/sqlbot-local/tools/sqlbot_eval/`。
- Windows开发目录：`D:\SQLBot-main\tools\sqlbot_eval\`。两处不是自动同步的，运行前确认版本相同。
- 统一入口：`cli.py`；旧 `collect.py` 是单题采集工具，本流程不用它。
- 本轮已完成的评分批次：`reports/single20_001/`，保留作为基线材料，不重新prepare或覆盖。

组员从仓库取得工具后，应在已能正常运行SQLBot的项目中操作。只有工具目录不够：run/collect还依赖后端环境和可访问的应用数据库。

## 2. 首次准备（只做一次）

准备两份Excel，放入项目的 `项目学习文档/数据集和测试问题集/`：

1. `单任务问题表 (2).xlsx`：包含题号、问题、成果要求、标准答案和业务口径。
2. `Text2SQL评分表模板.xlsx`：使用项目约定模板，不自行改表头。

业务表提前导入SQLBot，确认账号能选择数据源并正常问数。本项目使用“软件2404班及李思达老师所带班级学生成绩数据”。组员部署的数据源ID可能不同，不能照抄其他机器的ID。

以下都在 **Ubuntu终端** 执行。项目不在此位置的组员，只修改第一行：

```bash
cd /home/sqlbotdev/sqlbot-local
export EVAL_ROOT="$PWD"
export EVAL_PY="$EVAL_ROOT/backend/.venv/bin/python"
export EVAL_CLI="$EVAL_ROOT/tools/sqlbot_eval/cli.py"
"$EVAL_PY" -m pip install -r tools/sqlbot_eval/requirements.txt
test -f tools/sqlbot_eval/config.local.json || cp tools/sqlbot_eval/config.example.json tools/sqlbot_eval/config.local.json
nano tools/sqlbot_eval/config.local.json
```

在编辑器中确认 base_url（包含 `/api/v1`）、datasource_name，填写实际datasource_id或保留null按名称查找；rubric_version改为 `v2`；记录data_version、deployment_version、model_version。Ctrl+O、Enter保存，Ctrl+X退出。不要将token放进配置。

重新打开终端后，需要重新执行cd和上面三行export变量设置。

## 3. 设置登录令牌（每次终端会话）

浏览器登录SQLBot，F12 → Network，刷新数据源列表，在请求头复制 `X-SQLBOT-TOKEN` 的值，仅复制值。

**单独执行**下面一行，再粘贴token并回车。输入不显示是正常的：

```bash
read -rsp '粘贴SQLBot token后按回车：' SQLBOT_TOKEN
```

随后执行：

```bash
export SQLBOT_TOKEN
"$EVAL_PY" -c "import os; t=os.environ.get('SQLBOT_TOKEN',''); print('格式通过' if t and t.isascii() and '\n' not in t and '\r' not in t else '令牌为空或格式错误')"
```

格式通过不代表令牌未过期；401时重新登录取值。不要把token发给组员或AI。

## 4. 创建一个新批次

每次新实验只执行一次下面整段。批次名自动包含时间，避免撞上已有目录：

```bash
export EVAL_BATCH_ID="single20_$(date +%Y%m%d_%H%M%S)"
export EVAL_BATCH="$EVAL_ROOT/tools/sqlbot_eval/reports/$EVAL_BATCH_ID"
"$EVAL_PY" "$EVAL_CLI" prepare \
  --config "$EVAL_ROOT/tools/sqlbot_eval/config.local.json" \
  --questions "$EVAL_ROOT/项目学习文档/数据集和测试问题集/单任务问题表 (2).xlsx" \
  --ids S002,S003,S004,S009,S010,S016,S017,S018,S019,S020,S021,S022,S029,S030,S031,S034,S039,S040,S044,S045 \
  --batch-id "$EVAL_BATCH_ID" \
  --rubric "$EVAL_ROOT/tools/sqlbot_eval/templates/rubric_v2.txt" \
  --template "$EVAL_ROOT/项目学习文档/数据集和测试问题集/Text2SQL评分表模板.xlsx"
printf '本轮目录：%s\n' "$EVAL_BATCH"
```

记下输出的批次目录。以上是当前题库前20题的实际题号，不是S001至S020。prepare失败先停止；目录存在不代表准备完整，不直接删除或继续运行。检查manifest.json、cases.json及原先输出，确认是之前成功准备的同一实验后才恢复。

恢复已有批次：重新设置第2节的环境变量，再执行 `export EVAL_BATCH="$EVAL_ROOT/tools/sqlbot_eval/reports/实际批次名"`，不要重新生成批次名或prepare。

## 5. 自动问数并生成提示词

```bash
"$EVAL_PY" "$EVAL_CLI" run --batch "$EVAL_BATCH"
```

这一步会调用SQLBot和模型。不要并行开第二个run。结束后检查20题均为success或failed，其他状态为0，paused为null，collection_errors为空。SQL失败可以继续评分；链路状态未知不能当成SQL失败。

若暂停，先检查服务和错误输出；再次run会先核对原请求。不要换批次掩盖未完成请求。仅补采已存在请求的数据可以运行：

```bash
"$EVAL_PY" "$EVAL_CLI" collect --batch "$EVAL_BATCH"
```

采集完成后：

```bash
"$EVAL_PY" "$EVAL_CLI" review --batch "$EVAL_BATCH"
"$EVAL_PY" -c "import os,json,pathlib; b=pathlib.Path(os.environ['EVAL_BATCH']); [(b/'scores'/c['question_id']).mkdir(parents=True,exist_ok=True) for c in json.loads((b/'cases.json').read_text())]; print('评分目录已准备好')"
```

review应生成20份提示词、errors为空。collect/review/export都不会重新问数。

## 6. AI评分：任选一种

### A. 自己发送给其他AI

在Ubuntu执行 `explorer.exe "$(wslpath -w "$EVAL_BATCH")"` 打开本轮文件夹；也可在Windows资源管理器地址栏输入 `\\wsl.localhost\Ubuntu\home\sqlbotdev\sqlbot-local\tools\sqlbot_eval\reports`，进入本轮目录。

1. 打开 `prompts/S002/run_001.txt`，把完整文本复制到新的AI对话，或拖入该文件。不要只发送SQL或截图。
2. 要求按照其中规则输出完整JSON。标准答案和口径应该已经在提示词中，若缺失先修采集材料，不让AI猜标准。
3. 将JSON保存为 `scores/S002/run_001.json`。记事本另存为：所有文件、UTF-8，不能变成 `.json.txt`，不保留Markdown代码围栏。
4. 对其余题重复。题号和run号对应原提示词，不自行编号。尽量统一评分模型，并在本轮目录记一份 `reviewer.txt`，说明模型、日期和人工复核情况。

### B. 让Codex逐题评分并保存

把实际批次目录替换进下面指令，发送给能读取该目录的Codex：

> 请读取批次【填写完整批次目录】的manifest、cases、rubric、review_inputs和prompts。按照每份已生成提示词逐题评分，输出到本批次scores/<question_id>/<run_id>.json，自动创建目录。严格保持score_contract中的身份、版本和input_sha256；根据标准答案、口径及实际证据评分，不根据SQL执行成功推断答案正确，不补造证据。明确失败无答案按本批次规则判分。不要重新提交SQLBot问数，不更改原始材料，不覆盖已有评分；已有评分先校验并报告。最后运行cli.py export校验，并核对有效评分数等于预期运行数，报告缺失或错误。记录评分者和需要人工复核的题目。本次只保存评分JSON，不覆盖Excel。

这是由Codex读取材料并完成评分，不是脚本已接通外部模型API。不要提供SQLBot token，离线评分不需要。分享给其他AI前，确认材料中的学生信息处于团队允许的使用范围。

## 7. 校验评分（两种方式都要做）

```bash
"$EVAL_PY" "$EVAL_CLI" export --batch "$EVAL_BATCH"
"$EVAL_PY" -c "import os,json,pathlib; p=json.loads((pathlib.Path(os.environ['EVAL_BATCH'])/'export_plan.json').read_text()); rows=p['rows']; missing=[r['question_id']+'/'+r['run_id'] for r in rows if not r.get('score')]; print('有效评分:',len(rows)-len(missing),'/',len(rows)); print('缺失或无效:',missing)"
```

本批次没有补跑时应为20/20，同时errors和warnings为空。仅errors为空不代表评分齐全。JSON格式错误让AI修复格式；hash不符核对本题本次原提示词，不伪造或强行替换hash。修复评分后只重新export，不重新run。

## 8. 写入Excel

**export默认只生成export_plan.json，未写Excel。** 当前Excel导出还需要Node.js和 `@oai/artifact-tool`，这不是Python requirements能安装的依赖。首次交接先确认团队有可运行该库的导出环境；只有普通Python环境的组员可完成前7步，再把整批目录交给有该环境的组员或Codex导出。不要照搬他人电脑的Codex缓存路径。

依赖已就绪且可直接import时，在Ubuntu执行：

```bash
"$EVAL_PY" "$EVAL_CLI" export --batch "$EVAL_BATCH" \
  --output-xlsx "$EVAL_BATCH/scorecard.xlsx" --node node
```

如果库不在Node默认搜索路径，先设置 `SQLBOT_ARTIFACT_MODULE` 为实际库入口文件的绝对路径；不要猜路径。跨机器移交必须复制整批目录，并在目标机器重新export，更新模板快照的路径。

也可以给Codex这条指令：

> 请校验【完整批次目录】的全部评分，确认数量齐全、无错误后，使用项目export_scorecard.mjs和可用的artifact-tool环境生成scorecard.xlsx。保留输入模板及已有人工分；输出已存在时使用新文件名。检查生成文件的三个工作表及题号和AI分是否完整。依赖不可用请明确说明，不把export_plan.json当作Excel完成。

已有人工分时使用 `--base-xlsx 旧成绩单完整路径`，并输出到 `scorecard_v2.xlsx`，不可原地覆盖。AI分、人工分和裁定分分开，未填写裁定分时相关总分空白是正常的。

## 9. 一轮结束的检查清单

- 保存cases、rubric、template快照，mapping关联，trials原始证据，review_inputs及prompts。
- 20份有效scores，export_plan和最终成绩单；人工复核有争议的分数。
- 记录SQLBot代码版本、模型、数据/术语配置和评分者；优化后用新批次运行相同题目。
- 失败保留；补跑作为新run记录，不选择成功的一次悄悄替代首轮。

目录职责：`mapping.json`记录关联/状态；`trials/`存过程证据；`review_inputs/`存结构化评分材料；`prompts/`存完整提示词；`scores/`存评分JSON。实际映射文件名称以批次生成文件为准。

## 10. 后续提交GitHub的范围

提交工具源码、tests、templates规则、config.example.json和文档；真实学生数据、运行日志、token、本地配置及整批reports不直接上传。题库和成绩模板若要随仓库分发，另提供经过确认可共享的版本；新组员仍需按第2节准备这两份文件。

在项目根目录先检查，避免把本项目其他未完成改动一起提交：

```bash
git status --short
git add tools/sqlbot_eval
git diff --cached --stat
git diff --cached
```

确认暂存区仅包含预期工具文件、没有敏感内容后再commit/push。忽略规则不会自动移除已经跟踪的文件。当前SOP只整理交接方案，不代表已推送GitHub。
