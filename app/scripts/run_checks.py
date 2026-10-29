"""Run the shipped, explicit test suite in fresh local temporary databases."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,json,os,sys,subprocess
ROOT=Path(__file__).resolve().parents[1]
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--suite',choices=['security','core','bootstrap','all'],default='core');args=parser.parse_args()
 spec=json.loads((ROOT/'tests/release-suites.json').read_text(encoding='utf-8'))
 names=['test_release_seed.py','test_release_delivery.py']if args.suite=='bootstrap'else spec['security']+spec['core']if args.suite=='all'else spec[args.suite]
 names=list(dict.fromkeys(names))
 work=ROOT/'var/checks'/(args.suite+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
 for n in ['import','temp']:(work/n).mkdir(parents=True,exist_ok=True)
 env=os.environ.copy()
 for k in list(env):
  if k.startswith('ZX_') and any(x in k for x in ['API_KEY','DATABASE_URL','PG_TEST','EVIDENCE_DIR']):env.pop(k,None)
 env.update(ZX_TEST_IMPORT_DB_DIR=str(work/'import'),TEMP=str(work/'temp'),TMP=str(work/'temp'),
  ZX_DATABASE_URL='sqlite:///'+(work/'import/app.sqlite').as_posix(),ZX_KNOWLEDGE_DATABASE_URL='sqlite:///'+(work/'import/knowledge.sqlite').as_posix(),
  ZX_DEEPSEEK_API_KEY='',ZX_MODEL_API_KEY='',ZX_MODEL_PROVIDER='rule',PYTHONDONTWRITEBYTECODE='1',
  PYTHONPATH=os.pathsep.join([str(ROOT/'src'),str(ROOT/'tests')]))
 command=[sys.executable,'-m','pytest','-p','release_network_guard',*[str(ROOT/'tests'/n)for n in names],'-q',
  '--junitxml='+str(work/'results.xml'),'--basetemp='+str(work/'temp/pytest'),'-o','cache_dir='+str(work/'temp/cache')]
 code=subprocess.run(command,cwd=ROOT,env=env).returncode
 print('Test output:',work.relative_to(ROOT).as_posix());return code
if __name__=='__main__':raise SystemExit(main())
