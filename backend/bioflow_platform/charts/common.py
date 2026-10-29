import math
import numpy as np

def number(row,key):
    try:value=float(row[key])
    except (KeyError,ValueError,TypeError):raise ValueError('数值字段缺失或格式错误：'+key)
    if not math.isfinite(value):raise ValueError('数值必须有限：'+key)
    return value

def required(rows,*keys):
    if not rows or any(not str(row.get(key,'')).strip() for row in rows for key in keys):raise ValueError('缺少字段或内容为空：'+', '.join(keys))

def matrix(rows):
    required(rows,'sample');keys=[k for k in rows[0] if k not in {'sample','group','label'}]
    if len(rows)<2 or len(keys)<2 or len(rows)>2000 or len(keys)>2000:raise ValueError('矩阵需要 2–2000 个样本和 2–2000 个数值特征')
    if len({r['sample'] for r in rows})!=len(rows):raise ValueError('样本名称不能重复')
    return np.array([[number(r,k) for k in keys] for r in rows]),keys

def series(axis,key,label,artist):
    artist.set_label(label)
    if not hasattr(axis,'_bioflow_series'):axis._bioflow_series=[]
    axis._bioflow_series.append((key,label,artist))

def pvalue(row):
    p=number(row,'pvalue')
    if not 0<=p<=1:raise ValueError('P 值必须位于 0–1')
    return max(p,np.finfo(float).tiny)
