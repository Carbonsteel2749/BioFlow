"""Run with the existing numeric Linux Python; gateway does not import NumPy."""
import hashlib
import json
from pathlib import Path
import pytest
from backend.bioflow_platform.charts.registry import CHARTS

@pytest.mark.parametrize('chart',CHARTS,ids=lambda c:c['id'])
def test_registered_program_generates_three_formats_with_display_edits(tmp_path,chart):
    pytest.importorskip('matplotlib')
    from backend.bioflow_platform.chart_runner import render
    data=chart['example'].encode();(tmp_path/'source.csv').write_bytes(data)
    spec={'chart':chart['id'],'source':{'sha256':hashlib.sha256(data).hexdigest()},'parameters':{'title':'Custom title <safe>','x_label':'Custom X','y_label':'Custom Y'},'columns':{}}
    (tmp_path/'request.json').write_text(json.dumps(spec));render(tmp_path)
    result=json.loads((tmp_path/'result.json').read_text());assert set(result['outputs'])=={'png','svg','pdf'}
    svg=(tmp_path/'figure.svg').read_text();assert 'Custom title &lt;safe&gt;' in svg and 'Custom X' in svg and 'Custom Y' in svg
    assert (tmp_path/'source.csv').read_bytes()==data

def test_roc_ties_are_grouped_and_single_class_is_rejected(tmp_path):
    pytest.importorskip('matplotlib')
    from backend.bioflow_platform.chart_runner import render
    for data,good in [(b'label,score\n0,0.5\n1,0.5\n',True),(b'label,score\n1,0.5\n1,0.9\n',False)]:
        (tmp_path/'source.csv').write_bytes(data);(tmp_path/'request.json').write_text(json.dumps({'chart':'roc','source':{'sha256':hashlib.sha256(data).hexdigest()},'parameters':{},'columns':{}}))
        if good:
            render(tmp_path);assert 'AUC=0.500' in (tmp_path/'figure.svg').read_text()
        else:
            with pytest.raises(ValueError,match='两种真实标签'):render(tmp_path)

def test_matrix_column_mapping_and_legend_alias_preserve_raw_data(tmp_path):
    pytest.importorskip('matplotlib')
    from backend.bioflow_platform.chart_runner import render
    data=b'SampleID,F1,F2,Cohort\nA,1,2,T\nB,2,1,T\nC,4,5,C\n';(tmp_path/'source.csv').write_bytes(data)
    spec={'chart':'pca','source':{'sha256':hashlib.sha256(data).hexdigest()},'columns':{'sample':'SampleID','group':'Cohort'},'parameters':{'legend_labels':{'group:T':'Treatment'},'legend_order':['group:C','group:T'],'legend_position':'outside_right'}}
    (tmp_path/'request.json').write_text(json.dumps(spec));render(tmp_path)
    result=json.loads((tmp_path/'result.json').read_text());assert {r['id'] for r in result['legend']}=={'group:T','group:C'}
    svg=(tmp_path/'figure.svg').read_text();assert 'Treatment' in svg
    assert (tmp_path/'source.csv').read_bytes()==data

def test_enrichment_mapping_changes_coordinates_and_size_not_just_labels():
    pytest.importorskip('matplotlib')
    import matplotlib.pyplot as plt
    import numpy as np
    from backend.bioflow_platform.charts.enrichment import draw
    rows=[{'term':'A','count':'10','ratio':'0.1','pvalue':'0.01'},{'term':'B','count':'20','ratio':'0.4','pvalue':'0.001'}]
    fig,ax=plt.subplots();draw(rows,fig,ax,{'enrichment_x':'count','enrichment_size':'ratio'})
    assert np.allclose(ax.collections[0].get_offsets()[:,0],[10,20])
    assert np.allclose(ax.collections[0].get_sizes(),[95,320])
    assert ax.get_xlabel()=='Count'
    assert all('GeneRatio' in label for _,label,_ in ax._bioflow_series)
    plt.close(fig)


def test_common_canvas_and_font_are_applied_to_export(tmp_path):
    pytest.importorskip('matplotlib')
    from backend.bioflow_platform.chart_runner import render
    data=b'term,ratio,pvalue,count\nA,0.1,0.01,10\nB,0.4,0.001,20\n'
    (tmp_path/'source.csv').write_bytes(data)
    (tmp_path/'request.json').write_text(json.dumps({'chart':'enrichment','source':{'sha256':hashlib.sha256(data).hexdigest()},'columns':{},'parameters':{'width':12,'height':4,'font_size':16,'enrichment_x':'count','enrichment_size':'ratio'}}))
    render(tmp_path)
    result=json.loads((tmp_path/'result.json').read_text())
    assert result['canvas']=={'width':12,'height':4,'font_size':16}
    assert result['data_mapping']['x']=='count' and result['data_mapping']['size']=='ratio'
    svg=(tmp_path/'figure.svg').read_text()
    assert 'Count' in svg and 'GeneRatio = ' in svg
