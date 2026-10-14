"""Call pandas' table inspection and explicit keyed merge APIs."""
import argparse
import json
from pathlib import Path
import pandas as pd


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--operation',choices=['inspect','join'],required=True)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--right',type=Path)
    p.add_argument('--keys',default='subject_id')
    p.add_argument('--how',choices=['left','inner','outer'],default='left')
    p.add_argument('--validate',choices=['one_to_one','many_to_one'],default='one_to_one')
    p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args()
    keys=[x.strip() for x in a.keys.split(',') if x.strip()]
    if not keys or len(set(keys))!=len(keys):
        raise ValueError('Specify distinct join keys')
    def read(path, dtype=None):
        if path.suffix.lower() not in {'.csv','.tsv'}:
            raise ValueError('Select a CSV or TSV table')
        return pd.read_csv(path,sep='\t' if path.suffix.lower()=='.tsv' else ',',dtype=dtype)
    out=a.output_dir
    out.mkdir(parents=True,exist_ok=True)
    if a.operation=='inspect':
        table=read(a.input)
        report={'rows':len(table),'duplicate_rows':int(table.duplicated().sum()),
            'columns':[{'name':str(name),'dtype':str(table[name].dtype),
                'missing':int(table[name].isna().sum()),'unique':int(table[name].nunique()),
                'unique_nonnull_key_candidate':bool(len(table) and not table[name].isna().any() and table[name].is_unique)}
                for name in table.columns]}
    else:
        if not a.right:
            raise ValueError('Select the right-hand table')
        left,right=read(a.input,{key:str for key in keys}),read(a.right,{key:str for key in keys})
        if left[keys].isna().any().any() or right[keys].isna().any().any():
            raise ValueError('Join keys cannot be missing')
        merged=left.merge(right,on=keys,how=a.how,validate=a.validate,indicator=True)
        report={'left_rows':len(left),'right_rows':len(right),'output_rows':len(merged),
            'keys':keys,'how':a.how,'validate':a.validate,
            'matched':{str(k):int(v) for k,v in merged['_merge'].value_counts().items()}}
        merged.to_csv(out/'joined.csv',index=False)
    (out/'table_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
