from __future__ import annotations

import math
import copy
import re
from pathlib import Path

from .storage import artifact, load_runs, read_json, write_json


def evidence_exists(trial: dict, path: str) -> bool:
    try:
        value = trial
        for part in path.split('.'):
            value = value[int(part)] if isinstance(value, list) else value[part]
        return True  # An existing null field can prove no result was delivered.
    except (KeyError, ValueError, TypeError, IndexError):
        return False


def validate_score(score: dict, review: dict, allow_missing_hash: bool = False) -> dict:
    score = copy.deepcopy(score)
    contract = review['score_contract']
    for key in ['schema_version', 'rubric_version', 'input_sha256', 'batch_id', 'question_id', 'run_id', 'record_id']:
        if key == 'input_sha256' and key not in score and allow_missing_hash:
            score['needs_human_review'] = True
            continue
        if type(score.get(key)) is not type(contract[key]) or score.get(key) != contract[key]:
            raise ValueError(f'评分身份/版本不符：{key}')
    dimensions = score.get('dimensions')
    if not isinstance(dimensions, dict) or set(dimensions) != set(contract['dimensions']):
        raise ValueError('评分维度缺失或多余')
    for name, item in dimensions.items():
        if not isinstance(item, dict):
            raise ValueError(f'{name} 必须为对象')
        status, value = item.get('status'), item.get('score')
        if status == 'scored':
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError(f'{name} 分数必须为 0～100 有限数值')
        elif status not in {'insufficient_evidence', 'not_applicable'} or value is not None:
            raise ValueError(f'{name} 状态/空分数不合法')
        if not isinstance(item.get('reason'), str) or not item['reason'].strip():
            raise ValueError(f'{name} 缺少理由')
        refs = item.get('evidence')
        if not isinstance(refs, list) or (status == 'scored' and not refs):
            raise ValueError(f'{name} 缺少证据路径')
        normalized = []
        for ref in refs:
            if isinstance(ref, str):
                candidate = re.sub(r'（[^）]*）$', '', ref)
                if candidate.startswith('evidence.collection.'):
                    candidate = candidate[len('evidence.'):]
                if evidence_exists(review['trial'], candidate):
                    ref = candidate
            normalized.append(ref)
        item['evidence'] = normalized
        if any(not isinstance(p, str) or not evidence_exists(review['trial'], p) for p in normalized):
            raise ValueError(f'{name} 证据路径不存在或为空')
    if not isinstance(score.get('summary'), str) or type(score.get('needs_human_review')) is not bool:
        raise ValueError('summary 或 needs_human_review 格式错误')
    return score


def weighted_total(dimensions: dict, weights: dict, na_policy: str) -> float | None:
    if not weights:
        return None
    numerator = denominator = 0
    for name, weight in weights.items():
        item = dimensions[name]
        if item['status'] == 'insufficient_evidence':
            return None
        if item['status'] == 'not_applicable':
            if na_policy != 'exclude':
                return None
            continue
        numerator += item['score'] * weight
        denominator += weight
    return round(numerator / denominator, 2) if denominator else None


def export_plan(batch: Path, allow_missing_hash: bool = False) -> dict:
    manifest = read_json(batch / 'manifest.json')
    cases = {c['question_id']: c for c in read_json(batch / 'cases.json')}
    rows, errors, warnings = [], {}, {}
    for run in load_runs(batch):
        score, trial, total = None, None, None
        key = f'{run.question_id}/{run.run_id}'
        try:
            if artifact(batch, 'trials', run).exists():
                trial = read_json(artifact(batch, 'trials', run))
            if artifact(batch, 'scores', run).exists():
                score = validate_score(read_json(artifact(batch, 'scores', run)), read_json(artifact(batch, 'review_inputs', run)), allow_missing_hash)
                if 'input_sha256' not in score:
                    warnings[key] = '原始评分未提供输入哈希，材料版本未验证；按明确导入选项保留评分，需人工复核'
                total = weighted_total(score['dimensions'], manifest['config']['weights'], manifest['config']['na_policy'])
        except Exception as exc:
            errors[key] = str(exc)
            score = None
        rows.append({'batch_id': manifest['batch_id'], 'question_id': run.question_id, 'run_id': run.run_id,
                     'run_index': int(run.run_id.split('_')[-1]), 'question': cases[run.question_id]['question'],
                     'status': run.execution_status, 'collection_status': run.collection_status,
                     'score': score, 'ai_total': total, 'trial': trial, 'client_error': run.error,
                     'evidence_path': f'trials/{key}.json'})
    result = {'template': str((batch / 'template.xlsx').resolve()), 'rows': rows, 'errors': errors,
              'na_policy': manifest['config']['na_policy'], 'warnings': warnings}
    write_json(batch / 'export_plan.json', result)
    return result
