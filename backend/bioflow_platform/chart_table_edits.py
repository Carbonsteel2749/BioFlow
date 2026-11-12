"""Apply cell edits to a new chart snapshot, preserving original analysis files."""
import csv
import io


def edit_table(data,edits):
    text=data.decode('utf-8-sig')
    delimiter='\t' if '\t' in text.splitlines()[0] else ','
    reader=csv.DictReader(io.StringIO(text),delimiter=delimiter)
    columns=reader.fieldnames
    if not columns or len(set(columns))!=len(columns):raise ValueError('表头为空或重复')
    rows=[]
    for row in reader:
        if None in row or any(v is None for v in row.values()):raise ValueError('表格行列不一致')
        rows.append(row)
        if len(rows)>50000:raise ValueError('编辑绘图表格最多支持 50000 行')
    for edit in edits:
        if edit.row>=len(rows):raise ValueError(f'第 {edit.row+1} 行不存在')
        if edit.column not in columns:raise ValueError('编辑列不存在：'+edit.column)
        rows[edit.row][edit.column]=edit.value
    output=io.StringIO(newline='');writer=csv.DictWriter(output,fieldnames=columns,delimiter=delimiter)
    writer.writeheader();writer.writerows(rows)
    result=output.getvalue().encode('utf-8')
    if len(result)>10*1024**2:raise ValueError('编辑后表格超过 10 MB，请先筛选数据')
    return result
