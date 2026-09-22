"""Collect one completed SQLBot run into evaluation artifacts.

V1 is intentionally read-only. Run the question in SQLBot first, obtain its
ChatRecord id, then invoke this script with --record-id.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return str(value)


def model_dict(obj: Any, fields: list[str] | None = None) -> dict[str, Any]:
    names = fields or [k for k in vars(obj) if not k.startswith("_")]
    return {name: jsonable(getattr(obj, name, None)) for name in names}


def parse_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def load_question_spec(path: Path, question: str) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    if not path.exists():
        return {}, [f"问题表不存在：{path}"]
    try:
        import openpyxl
        workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
        sheet = workbook.worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        headers = [str(value).strip() if value is not None else "" for value in next(rows)]
        matches = []
        for row in rows:
            item = {headers[i]: jsonable(row[i]) for i in range(min(len(headers), len(row)))}
            if str(item.get("原始问题") or "").strip() == question.strip():
                matches.append(item)
        workbook.close()
        if not matches:
            return {}, [f"问题表中未找到完全匹配的问题：{question}"]
        if len(matches) > 1:
            warnings.append(f"问题表中找到 {len(matches)} 条相同问题，使用第一条")
        return matches[0], warnings
    except Exception as exc:
        return {}, [f"读取问题表失败：{exc}"]


def logs_by_operation(logs: list[Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for log in logs:
        operation = getattr(getattr(log, "operate", None), "name", None) or str(getattr(log, "operate", "UNKNOWN"))
        grouped[operation].append(model_dict(log, [
            "id", "pid", "operate", "ai_modal_id", "base_modal", "messages",
            "reasoning_content", "start_time", "finish_time", "token_usage",
            "local_operation", "error",
        ]))
    return dict(grouped)


def first_messages(grouped: dict[str, list[dict[str, Any]]], operation: str) -> list[Any]:
    rows = grouped.get(operation, [])
    return [row.get("messages") for row in rows]


def build_trial(record: Any, logs: list[Any], args: argparse.Namespace) -> dict[str, Any]:
    grouped = logs_by_operation(logs)
    record_data = model_dict(record, [
        "id", "chat_id", "request_id", "status", "create_time", "finish_time",
        "create_by", "datasource", "engine_type", "question", "sql_answer", "sql",
        "sql_exec_result", "data", "chart_answer", "chart", "finish", "error",
        "regenerate_record_id",
    ])
    sql_logs = grouped.get("GENERATE_SQL", [])
    execution_logs = grouped.get("EXECUTE_SQL", [])
    token_usage = [
        jsonable(getattr(log, "token_usage", None))
        for log in logs
        if getattr(log, "token_usage", None)
    ]
    duration_ms = None
    if record.create_time and record.finish_time:
        duration_ms = round((record.finish_time - record.create_time).total_seconds() * 1000, 3)

    status = "success" if record.finish and not record.error else ("failed" if record.error else "incomplete")
    warnings: list[str] = []
    if not grouped.get("FILTER_TERMS"):
        warnings.append("未找到 FILTER_TERMS 日志")
    if not grouped.get("CHOOSE_TABLE"):
        warnings.append("未找到 CHOOSE_TABLE 日志")
    if not sql_logs:
        warnings.append("未找到 GENERATE_SQL 日志")
    if not execution_logs:
        warnings.append("未找到 EXECUTE_SQL 日志")

    return {
        "identity": {
            "batch_id": args.batch_id,
            "question_id": args.question_id,
            "run_index": args.run_index,
            "record_id": record.id,
            "chat_id": record.chat_id,
            "collected_at": datetime.now().astimezone().isoformat(),
        },
        "input": {
            "question": args.question or record.question,
            "datasource_id": record.datasource,
            "engine_type": record.engine_type,
            "data_version": args.data_version,
        },
        "transcript": {
            "logs_by_operation": grouped,
            "retrieval": {
                "terms": first_messages(grouped, "FILTER_TERMS"),
                "sql_examples": first_messages(grouped, "FILTER_SQL_EXAMPLE"),
                "custom_prompts": first_messages(grouped, "FILTER_CUSTOM_PROMPT"),
                "datasource_selection": first_messages(grouped, "CHOOSE_DATASOURCE"),
                "table_schema": first_messages(grouped, "CHOOSE_TABLE"),
            },
            "sql_generation": {
                "prompt_messages": [row.get("messages") for row in sql_logs],
                "reasoning_content": [row.get("reasoning_content") for row in sql_logs],
                "token_usage": [row.get("token_usage") for row in sql_logs],
            },
            "validation": grouped.get("GENERATE_SQL_WITH_PERMISSIONS", []) + grouped.get("GENERATE_DYNAMIC_SQL", []),
            "execution_attempts": execution_logs,
        },
        "outcome": {
            "status": status,
            "final_sql": record.sql,
            "final_answer": parse_json(record.sql_answer),
            "data": parse_json(record.data),
            "sql_exec_result": parse_json(record.sql_exec_result),
            "error": record.error,
            "finish": record.finish,
        },
        "metrics": {
            "start_time": jsonable(record.create_time),
            "finish_time": jsonable(record.finish_time),
            "duration_ms": duration_ms,
            "token_usage": token_usage,
            "model_calls": len([
                log for log in logs
                if getattr(log, "ai_modal_id", None) is not None
            ]),
            "sql_attempts": len(sql_logs),
            "sql_repairs": max(0, len(sql_logs) - 1),
        },
        "collection": {"warnings": warnings, "parse_errors": []},
    }


def last_sql_messages(trial: dict[str, Any]) -> list[dict[str, Any]]:
    calls = trial["transcript"]["sql_generation"]["prompt_messages"]
    return calls[-1] if calls and isinstance(calls[-1], list) else []


def build_review_input(
    trial: dict[str, Any], args: argparse.Namespace, question_spec: dict[str, Any]
) -> dict[str, Any]:
    messages = last_sql_messages(trial)
    current_question_message = next((
        message for message in reversed(messages)
        if message.get("type") == "human" and not message.get("sqlbot_system", False)
    ), None)
    raw_model_output = next((
        message.get("content") for message in reversed(messages)
        if message.get("type") == "ai" and not message.get("sqlbot_system", False)
    ), None)
    raw_data = trial["outcome"]["data"]
    result_summary = raw_data
    if isinstance(raw_data, dict):
        result_summary = {
            "fields": raw_data.get("fields"),
            "rows": raw_data.get("data"),
            "row_count": len(raw_data.get("data") or []),
            "datasource_id": raw_data.get("datasource"),
        }
    return {
        "question": {
            "id": args.question_id,
            "text": trial["input"]["question"],
            "source_sequence": question_spec.get("序号"),
            "scenario": question_spec.get("使用场景"),
            "expected_answer": question_spec.get("预期答案"),
            "business_rules": question_spec.get("业务口径"),
            "expected_datasource": question_spec.get("数据来源"),
            "expected_behavior": question_spec.get("预期行为"),
        },
        "evidence": {
            "retrieved_terms": trial["transcript"]["retrieval"]["terms"],
            "configured_datasource_id": trial["input"]["datasource_id"],
            "datasource_selection_logs": trial["transcript"]["retrieval"]["datasource_selection"],
            "datasource_selection_note": "日志为空可能表示会话已固定数据源，不等于数据源召回失败",
            "schema": trial["transcript"]["retrieval"]["table_schema"],
            "current_question_message": current_question_message,
            "raw_model_output": raw_model_output,
            "sql": trial["outcome"]["final_sql"],
            "validation": trial["transcript"]["validation"],
            "execution": trial["transcript"]["execution_attempts"],
            "final_answer": trial["outcome"]["final_answer"],
            "result": result_summary,
        },
        "automatic_checks": {
            "sql_executed": trial["outcome"]["finish"],
            "safe_sql": None,
            "authorized_tables": None,
            "has_result": trial["outcome"]["data"] is not None,
            "duration_ms": trial["metrics"]["duration_ms"],
            "token_usage": trial["metrics"]["token_usage"],
        },
        "collection_warnings": trial["collection"]["warnings"],
    }


def prompt_text(review: dict[str, Any], args: argparse.Namespace) -> str:
    evidence = json.dumps(review, ensure_ascii=False, indent=2)
    return f'''你是 SQLBot 校园问数评测员。请依据题目要求、运行证据和评分规则评分，不得根据常识猜测。

评分规则：
1. 所有适用维度使用 0～100 分。100=完全满足；75=主体满足但有轻微缺口；50=约完成一半或有重要缺口；25=仅少量可用；0=未完成或结果错误。
2. 结果正确性：以预期答案和业务口径为准，核对值、人员、筛选条件、粒度和去重。
3. 任务完成度：只检查用户要求的结果是否全部交付；不要因结果数值错误而在此重复扣分。
4. 表达与可核验性：检查范围、字段含义和结果是否清楚，是否能从证据复核。
5. SQL 执行：以 execution 和 automatic_checks 为准。成功执行且返回预期结构为100；最终失败为0；不要只看SQL文本猜测。
6. 信息召回：检查最终 schema 是否覆盖必要表和字段，同时检查召回术语是否相关。无业务术语也能正确完成的问题，不因缺少术语而扣分；明显无关术语可扣分，但不得因此扣减结果正确性。
7. 数据源选择日志为空可能是会话固定了数据源，不能据此判定数据源召回失败。
8. 异常处理：只有确实发生异常、重试或部分失败时才评分；未发生异常时 score 输出 null、applicable 输出 false。
9. 证据不足时 score 输出 null 并在 unknowns 说明，不得猜测；每项扣分必须引用 evidence 或 automatic_checks 中的具体字段。

请输出严格 JSON，不要输出 Markdown：
{{
  "question_id": "{args.question_id}",
  "business": {{
    "correctness": {{"score": null, "evidence": []}},
    "completion": {{"score": null, "evidence": []}},
    "verifiability": {{"score": null, "evidence": []}}
  }},
  "execution": {{
    "sql_execution": {{"score": null, "evidence": []}},
    "information_recall": {{"score": null, "evidence": []}},
    "error_handling": {{"score": null, "applicable": false, "evidence": []}}
  }},
  "status": "success|partial_success|failed|unknown",
  "deductions": [],
  "unknowns": [],
  "needs_human_review": false
}}

本题运行证据：
{evidence}
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-id", type=int, required=True)
    parser.add_argument("--question-id", required=True, help="自定义题号，例如 S001")
    parser.add_argument("--batch-id", default="simulation_001")
    parser.add_argument("--run-index", type=int, default=1)
    parser.add_argument("--question")
    parser.add_argument("--data-version")
    parser.add_argument("--questions-file", help="问题 Excel；默认自动查找单任务问题表.xlsx")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    backend = root / "backend"
    os.chdir(backend)
    sys.path.insert(0, str(backend))

    from sqlmodel import Session, select
    from common.core.db import engine
    from apps.chat.models.chat_model import ChatRecord, ChatLog

    with Session(engine) as session:
        record = session.get(ChatRecord, args.record_id)
        if record is None:
            raise SystemExit(f"找不到 ChatRecord id={args.record_id}")
        logs = list(session.exec(select(ChatLog).where(ChatLog.pid == args.record_id).order_by(ChatLog.start_time, ChatLog.id)))

    trial = build_trial(record, logs, args)
    questions_path = Path(args.questions_file) if args.questions_file else root / "项目学习文档" / "数据集和测试问题集" / "单任务问题表.xlsx"
    question_spec, question_warnings = load_question_spec(questions_path, trial["input"]["question"])
    trial["collection"]["warnings"].extend(question_warnings)
    trial["input"]["question_spec_source"] = str(questions_path)
    review = build_review_input(trial, args, question_spec)
    output = Path(args.output_dir) if args.output_dir else Path(__file__).parent / "reports" / args.batch_id
    (output / "trials").mkdir(parents=True, exist_ok=True)
    (output / "review_inputs").mkdir(parents=True, exist_ok=True)
    (output / "prompts").mkdir(parents=True, exist_ok=True)
    qid = args.question_id
    (output / "trials" / f"{qid}.json").write_text(json.dumps(trial, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "review_inputs" / f"{qid}.json").write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "prompts" / f"{qid}.txt").write_text(prompt_text(review, args), encoding="utf-8")
    manifest = {
        "batch_id": args.batch_id,
        "question_id": qid,
        "record_id": args.record_id,
        "created_at": datetime.now().astimezone().isoformat(),
        "read_only": True,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成：{output / 'trials' / (qid + '.json')}")
    print(f"已生成：{output / 'review_inputs' / (qid + '.json')}")
    print(f"已生成：{output / 'prompts' / (qid + '.txt')}")
    if trial["collection"]["warnings"]:
        print("采集警告：")
        for warning in trial["collection"]["warnings"]:
            print(f"- {warning}")


if __name__ == "__main__":
    main()
