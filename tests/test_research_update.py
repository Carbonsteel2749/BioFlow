import hashlib
import json
import sqlite3
import pytest
from deploy.apply_research_update import apply

def fixture(tmp_path,relative='frontend/research-workspace.js'):
    target=tmp_path/'BioFlow';(target/'runtime').mkdir(parents=True);(target/'Start-BioFlow.sh').write_text('#!/bin/bash\n')
    with sqlite3.connect(target/'runtime/bioflow.sqlite3') as db:db.execute('CREATE TABLE kept(value TEXT)');db.execute("INSERT INTO kept VALUES('research')")
    old=target/'frontend/research-workspace.js';old.parent.mkdir();old.write_text('previous code')
    package=tmp_path/'update';source=package/'payload'/relative;source.parent.mkdir(parents=True);source.write_text('new code')
    (package/'UPDATE-MANIFEST.json').write_text(json.dumps({'files':[{'path':relative,'sha256':hashlib.sha256(b'new code').hexdigest()}]}))
    return package,target,old

def test_code_update_backs_up_code_and_preserves_project_database(tmp_path):
    package,target,old=fixture(tmp_path);backup=apply(package,target)
    assert old.read_text()=='new code'
    assert (backup/'files/frontend/research-workspace.js').read_text()=='previous code'
    with sqlite3.connect(target/'runtime/bioflow.sqlite3') as db:assert db.execute('SELECT value FROM kept').fetchone()[0]=='research'
    with sqlite3.connect(backup/'bioflow.sqlite3') as db:assert db.execute('SELECT value FROM kept').fetchone()[0]=='research'

def test_update_cannot_overwrite_runtime_files(tmp_path):
    package,target,old=fixture(tmp_path,'runtime/bioflow.sqlite3')
    with pytest.raises(ValueError,match='运行数据'):apply(package,target)
    assert old.read_text()=='previous code'
