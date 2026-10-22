"""Reviewed procedural knowledge -> bounded, advisory figure plans; no generated code."""
from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from pathlib import Path

REGISTRY = Path(__file__).resolve().parents[2] / 'assets/figure_recipes.json'


def _request_local_model(payload):
    # Fixed loopback destination: never accept a model URL from uploaded metadata.
    request = urllib.request.Request('http://127.0.0.1:11434/api/chat',
        data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read(16385)
    if len(raw) > 16384:
        raise ValueError('model response exceeds size bound')
    envelope = json.loads(raw)
    return json.loads(envelope['message']['content'])


def build_figure_plan(analysis, profile, *, ai_advice=False, request=None):
    raw = REGISTRY.read_bytes()
    catalog = json.loads(raw)
    eligible, skipped = [], {}
    for name, recipe in catalog['recipes'].items():
        if recipe['analysis'] != analysis:
            continue
        if profile['sample_count'] < recipe['minimum_samples'] or profile['feature_count'] < recipe['minimum_features']:
            skipped[name] = f"requires >= {recipe['minimum_samples']} quantifiable samples and >= {recipe['minimum_features']} positive features"
        else:
            eligible.append(name)
    result = {'registry_version': catalog['version'], 'registry_sha256': hashlib.sha256(raw).hexdigest(),
              'skill_sources': catalog['skill_sources'], 'profile': profile, 'source': 'rules',
              'selected_recipes': eligible, 'skipped_recipes': skipped,
              'ai_status': 'disabled', 'statistical_tests': 'not_performed',
              'vision_review': 'not_performed', 'journal_compliance': 'not_certified'}
    if not ai_advice or not eligible:
        return result
    # Model gets aggregate counts only: no sample IDs, group labels, paths or input rows.
    safe_profile = {key: profile[key] for key in ('sample_count', 'feature_count')}
    safe_profile['group_sizes'] = sorted(profile.get('group_counts', {}).values())
    payload = {'model': os.environ.get('BIOLLM_OLLAMA_MODEL', 'qwen3:14b'),
        'stream': False, 'think': False, 'format': 'json',
        'options': {'temperature': 0, 'num_predict': 120, 'num_ctx': 4096},
        'messages': [
            {'role': 'system', 'content': 'Choose only eligible chart IDs. Return exactly one JSON object with selected_recipes (a nonempty list of distinct eligible IDs). Never output code, numeric conclusions or extra fields.'},
            {'role': 'user', 'content': json.dumps({'reviewed_skill_guidance': catalog['guidance'],
                'anonymous_profile': safe_profile, 'eligible_recipes': eligible}, ensure_ascii=False)}]}
    result['prompt_sha256'] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    result['model'] = payload['model']
    try:
        proposal = (request or _request_local_model)(payload)
    except Exception as exc:
        result.update(ai_status='unavailable', ai_warning=f'{type(exc).__name__}; deterministic figure rules retained')
        return result
    selected = proposal.get('selected_recipes') if isinstance(proposal, dict) else None
    if (not isinstance(proposal, dict) or set(proposal) != {'selected_recipes'} or
        not isinstance(selected, list) or not selected or not all(isinstance(x, str) for x in selected) or
        len(selected) > len(eligible) or len(set(selected)) != len(selected) or any(x not in eligible for x in selected)):
        result.update(ai_status='invalid_response', ai_warning='unsupported or unsafe figure proposal; deterministic rules retained')
        return result
    result.update(source='ollama', ai_status='validated', selected_recipes=selected)
    for name in eligible:
        if name not in selected:
            result['skipped_recipes'][name] = 'not selected by validated advisory plan'
    return result
