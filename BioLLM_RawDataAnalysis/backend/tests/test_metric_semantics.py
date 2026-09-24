import json

import pytest

from backend.app.services.previews import _qc_summary


@pytest.mark.parametrize('metrics,expected', [
    (None, None),
    ({'input_pair_count': 80, 'retained_pair_count': 60, 'removed_pair_count': 20}, 25.0),
    ({'input_pair_count': 0, 'retained_pair_count': 0, 'removed_pair_count': 0}, None),
    ({'input_pair_count': 80, 'retained_pair_count': 90, 'removed_pair_count': -10}, None),
])
def test_qc_uses_actual_pair_counts_or_unknown(tmp_path, metrics, expected):
    fastp = tmp_path / 'S.fastp.json'
    fastp.write_text(json.dumps({'summary': {'before_filtering': {'total_reads': 200}, 'after_filtering': {'total_reads': 160}}}))
    if metrics is not None:
        metrics['sample_id'] = 'S'
        (tmp_path / 'S.host_depletion.metrics.json').write_text(json.dumps(metrics))
    assert _qc_summary(tmp_path)[0]['host_removed_pct'] == expected
