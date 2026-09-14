"""Registry-driven entry point to unchanged verified underlying replay."""
from pathlib import Path
import json,subprocess,sys,tempfile
from .registry import Registry
from .models import symbol


def replay(strategy_id, underlying, direction, release_zip, out_dir):
    item=Registry().get(strategy_id);underlying=symbol(underlying)
    if direction not in ('LONG','SHORT'):raise ValueError('invalid direction')
    code=Path(__file__).resolve().parents[1]/'research/aggressive_payoff/code'
    out=Path(out_dir).resolve();out.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        rule=Path(tmp)/'rule.json';rule.write_text(json.dumps(item['config']))
        subprocess.run([sys.executable,str(code/'replay_job.py'),'--zip',str(Path(release_zip).resolve()),'--rule',str(rule),'--symbol',underlying,'--direction',direction,'--out',str(out)],check=True)
    metadata={'strategy_id':strategy_id,'strategy_hash':item['sha256'],'case_id':item['case_id'],'data_tag':item['config']['data_tag'],'data_sha256':item['config']['sha256'],'disclaimer':'underlying proxy, not true option PnL'}
    (out/'strategy_metadata.json').write_text(json.dumps(metadata,indent=2)+'\n');return metadata
