"""Run real policy/crypto code offline; write all artifacts under E-drive project."""
from pathlib import Path
import subprocess,json,os,shutil

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'var/offline-checks';OUT.mkdir(parents=True,exist_ok=True)
JAVA=Path(shutil.which('java')).parent
env=os.environ.copy();env.update(TEMP=str(ROOT/'var/temp'),TMP=str(ROOT/'var/temp'),PYTHONIOENCODING='utf-8')
def run(args):
 p=subprocess.run([str(x)for x in args],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace')
 if p.returncode:raise RuntimeError(p.stdout+'\n'+p.stderr)
 return p.stdout.strip()
run([JAVA/'javac.exe','-encoding','UTF-8','-source','8','-target','8','-d',OUT,
 ROOT/'android-client/src/com/zhenxing/agent/local/LocalOrigin.java',ROOT/'android-client/tests/LocalOriginChecks.java'])
policy=json.loads(run([JAVA/'java.exe','-cp',OUT,'LocalOriginChecks']))
compat=json.loads(run([shutil.which('node'),ROOT/'android-client/tests/compat-checks.mjs']))
result={'local_origin':policy,'compatibility':compat,'network_calls':0,'database_access':False}
(OUT/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
