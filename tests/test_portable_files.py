import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3

import pytest
from fastapi import HTTPException
from backend.bioflow_platform import assets,project_files
from backend.bioflow_platform.store import Store


def test_dataset_files_survive_root_move_and_enforce_ownership(tmp_path,monkeypatch):
    original=tmp_path/'original';moved=tmp_path/'moved'
    incoming=original/'BioLLM_RawDataAnalysis/runtime/incoming'
    folder=incoming/'manifests';folder.mkdir(parents=True)
    (folder/'R1.fastq').write_bytes(b'@a\nAC\n+\n!!\n')
    (folder/'R2.fastq').write_bytes(b'@a\nTG\n+\n!!\n')
    (folder/'samples.csv').write_text('sample_id,read1,read2\nSRR1,R1.fastq,R2.fastq\n')
    store=Store(tmp_path/'portal.sqlite3');project=store.create('Study','','owner')
    store.claim('dataset','dataset','owner',{'dataset_id':'dataset','relative_manifest':'manifests/samples.csv'})
    store.link(project['id'],'dataset','dataset')
    shutil.move(original,moved);monkeypatch.delenv('BIOFLOW_ANALYSIS_ROOT',raising=False);monkeypatch.setattr(assets,'ROOT',moved)
    result=project_files.dataset_files(store,project['id'],'owner','dataset')
    assert result['status']=='available'
    assert result['samples'][0]['read1']['size_bytes']==11
    assert result['samples'][0]['read2']['relative_path']=='manifests/R2.fastq'
    with pytest.raises(HTTPException) as error:project_files.dataset_files(store,project['id'],'other','dataset')
    assert error.value.status_code==404
    with pytest.raises(HTTPException):project_files.input_path('../../private')


def test_sqlite_paths_are_stored_relative_and_relocated(tmp_path):
    source=Path(__file__).resolve().parents[1]/'BioLLM_RawDataAnalysis/backend/app/portable_db.py'
    spec=importlib.util.spec_from_file_location('portable_db_test',source);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    first=tmp_path/'first/runtime/state/tasks.sqlite3';first.parent.mkdir(parents=True)
    with sqlite3.connect(first) as db:
        db.execute('CREATE TABLE files(path TEXT,metadata TEXT)')
        db.execute('INSERT INTO files VALUES(?,?)',('/home/xh/BioLLM_RawDataAnalysis/runtime/incoming/sample.fastq',json.dumps({'path':'/home/xh/BioLLM_RawDataAnalysis/runtime/outputs/chart.svg'})))
    with module.connect(first) as db:db.execute('SELECT * FROM files').fetchone()
    with sqlite3.connect(first) as db:
        row=db.execute('SELECT * FROM files').fetchone()
        assert row[0]=='@runtime/incoming/sample.fastq'
        assert json.loads(row[1])['path']=='@runtime/outputs/chart.svg'
    second=tmp_path/'second/runtime/state/tasks.sqlite3';second.parent.mkdir(parents=True);shutil.copy2(first,second)
    with module.connect(second) as db:
        row=db.execute('SELECT * FROM files').fetchone()
        assert Path(row[0])==second.parent.parent/'incoming/sample.fastq'
        assert Path(json.loads(row[1])['path'])==second.parent.parent/'outputs/chart.svg'
