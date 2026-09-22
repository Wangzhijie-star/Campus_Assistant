from __future__ import annotations

import json
from pathlib import Path
from .storage import artifact, digest, load_runs, read_json, write_json, atomic_text


def build_review(case: dict, trial: dict, manifest: dict, rubric: str, template_sha256: str = '') -> dict:
    contract = {'schema_version': '1.0', 'rubric_version': manifest['config']['rubric_version'],
                **{k: trial['identity'][k] for k in ['batch_id', 'question_id', 'run_id', 'record_id']},
                'dimensions': {d: {'status': 'insufficient_evidence', 'score': None, 'reason': '请填写理由', 'evidence': []}
                               for d in manifest['config']['dimensions']},
                'summary': '', 'needs_human_review': True}
    value = {'question': case, 'rubric': rubric, 'trial': trial, 'template_sha256': template_sha256, 'score_contract': contract}
    contract['input_sha256'] = digest(value)
    return value


def prepare_reviews(batch: Path) -> dict:
    manifest = read_json(batch / 'manifest.json')
    cases = {c['question_id']: c for c in read_json(batch / 'cases.json')}
    rubric = (batch / 'rubric.txt').read_text(encoding='utf-8')
    template_path = batch / 'prompt_template.txt'
    if not template_path.exists():
        atomic_text(template_path, (Path(__file__).parent / 'templates/review_prompt.txt').read_text(encoding='utf-8'))
    template = template_path.read_text(encoding='utf-8')
    errors, generated = {}, []
    for run in load_runs(batch):
        key = f'{run.question_id}/{run.run_id}'
        try:
            trial = read_json(artifact(batch, 'trials', run))
            review = build_review(cases[run.question_id], trial, manifest, rubric, digest(template))
            text = template + json.dumps(review, ensure_ascii=False, indent=2)
            if len(text) > manifest['config']['max_prompt_chars']:
                raise ValueError('材料超过长度上限；未截断，请人工检查后决定上限或材料范围')
            target = artifact(batch, 'review_inputs', run)
            if target.exists() and read_json(target) != review:
                raise ValueError('已有不同评分材料，不覆盖；请建立新的材料版本')
            write_json(target, review)
            atomic_text(artifact(batch, 'prompts', run, 'txt'), text)
            generated.append(key)
        except Exception as exc:
            errors[key] = str(exc)
    atomic_text(batch / 'prompt_index.md', '# 评分材料索引\n\n' + '\n'.join(f'- [{k}](prompts/{k}.txt)' for k in generated)
                + '\n\n异常：\n' + json.dumps(errors, ensure_ascii=False, indent=2))
    return {'generated': generated, 'errors': errors}
