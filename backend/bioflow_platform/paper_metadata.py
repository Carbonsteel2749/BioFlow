"""Conservative local bibliographic fields; unprovided values stay empty."""
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree

def extract(path,name):
    result={'title':Path(name).stem,'authors':'','year':'','doi':'','tags':'','metadata_source':'文件名；可手动补全'}
    suffix=Path(name).suffix.lower();fields={};text=''
    try:
        if suffix=='.docx':
            with zipfile.ZipFile(path) as archive:
                info=archive.getinfo('docProps/core.xml')
                if info.file_size<=65536:
                    root=ElementTree.fromstring(archive.read(info));fields={e.tag.rsplit('}',1)[-1]:(e.text or '').strip() for e in root}
            result.update(title=fields.get('title') or result['title'],authors=fields.get('creator',''))
        elif suffix=='.pdf':
            try:
                from pypdf import PdfReader
                if path.stat().st_size<=50*1024**2:
                    metadata=PdfReader(path).metadata
                    if metadata:fields={'Title':metadata.title or '','Author':metadata.author or ''}
            except Exception:pass
            if not fields and shutil.which('pdfinfo'):
                parsed=subprocess.run(['pdfinfo',str(path)],capture_output=True,text=True,timeout=8)
                if parsed.returncode==0:fields={r.split(':',1)[0].strip():r.split(':',1)[1].strip() for r in parsed.stdout.splitlines() if ':' in r}
            result.update(title=fields.get('Title') or result['title'],authors=fields.get('Author',''))
        elif suffix in {'.ris','.bib','.md','.txt'}:
            with path.open(encoding='utf-8-sig',errors='replace') as handle:text=handle.read(32768)
            if suffix=='.ris':
                for line in text.splitlines():
                    m=re.match(r'^(TI|T1|AU|A1|PY|Y1|DO)\s+-\s*(.*)',line)
                    if m:fields.setdefault(m[1],[]).append(m[2].strip())
                result.update(title=(fields.get('TI') or fields.get('T1') or [result['title']])[0],authors='; '.join(fields.get('AU') or fields.get('A1') or []),year=(fields.get('PY') or fields.get('Y1') or [''])[0][:4],doi=(fields.get('DO') or [''])[0])
            elif suffix=='.bib':
                for key,field in [('title','title'),('authors','author'),('year','year'),('doi','doi')]:
                    match=re.search(r'\b'+field+r'\s*=\s*[{"]([^}\"]+)',text,re.I)
                    if match:result[key]=match[1].strip()
        if fields:result['metadata_source']='文件题录信息；请核对'
        if not re.fullmatch(r'[12]\d{3}',result['year']):result['year']=''
        result['title']=result['title'][:500];result['authors']=result['authors'][:1000];result['doi']=result['doi'][:250]
    except (OSError,ValueError,KeyError,zipfile.BadZipFile,ElementTree.ParseError,subprocess.SubprocessError):pass
    return result
