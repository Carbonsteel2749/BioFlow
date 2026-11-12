import csv,hashlib,io
import pytest
from backend.bioflow_platform.chart_contracts import TableEdit
from backend.bioflow_platform.chart_table_edits import edit_table


def test_cell_edit_keeps_other_values_and_original_bytes():
    original=b'sample\tG1\tG2\nA\t1\t2\nB\t3\t4\n';before=hashlib.sha256(original).hexdigest()
    result=edit_table(original,[TableEdit(row=1,column='G2',value='99.5')])
    rows=list(csv.DictReader(io.StringIO(result.decode()),delimiter='\t'))
    assert rows==[{'sample':'A','G1':'1','G2':'2'},{'sample':'B','G1':'3','G2':'99.5'}]
    assert hashlib.sha256(original).hexdigest()==before


def test_cell_edit_rejects_unknown_coordinates_and_preserves_csv_quoting():
    data=b'sample,note,value\nA,"hello, world",2\n'
    edited=edit_table(data,[TableEdit(row=0,column='note',value='line one\nline two, quoted')])
    assert list(csv.DictReader(io.StringIO(edited.decode())))[0]['note']=='line one\nline two, quoted'
    with pytest.raises(ValueError,match='不存在'):edit_table(data,[TableEdit(row=2,column='value',value='4')])
    with pytest.raises(ValueError,match='不存在'):edit_table(data,[TableEdit(row=0,column='unknown',value='4')])
