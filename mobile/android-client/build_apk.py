"""Reproducible SDK CLI build, with a persistent private local signing key."""
from pathlib import Path
import subprocess,zipfile,os,secrets,hashlib,json,shutil

ROOT=Path(__file__).resolve().parents[1];SRC=ROOT/'android-client';BUILD=ROOT/'var/android-build';TOOLS=BUILD/'toolchain'
OUT=ROOT/'dist'
JAVA=Path(shutil.which('java')).parent
BT=next(TOOLS.glob('build-tools/*/aapt2.exe')).parent
JAR=next(TOOLS.glob('platform/*/android.jar'))
WORK=BUILD/'apk';WORK.mkdir(parents=True,exist_ok=True);OUT.mkdir(parents=True,exist_ok=True)
TEMP=ROOT/'var/temp';TEMP.mkdir(parents=True,exist_ok=True)
os.environ['TEMP']=os.environ['TMP']=str(TEMP)
os.environ['JAVA_TOOL_OPTIONS']='-Djava.io.tmpdir='+str(TEMP)+' -Duser.language=en -Dfile.encoding=UTF-8'
def run(args):
 p=subprocess.run([str(x)for x in args],capture_output=True,text=True,encoding='utf-8',errors='replace')
 if p.returncode:raise RuntimeError(p.stdout+'\n'+p.stderr)
 return p.stdout
for folder in ['generated','classes','dex']:(WORK/folder).mkdir(exist_ok=True)
assert (SRC/'assets/web/index.html').is_file(), 'Build the bundled web frontend first: cd web; npm ci; npm run build'
run([BT/'aapt2.exe','compile','--dir',SRC/'res','-o',WORK/'resources.zip'])
run([BT/'aapt2.exe','link','-I',JAR,'--manifest',SRC/'AndroidManifest.xml','--java',WORK/'generated','-A',SRC/'assets',
 '--min-sdk-version','28','--target-sdk-version','35','--version-code','2','--version-name','0.1.1',
 '-o',WORK/'resources.apk',WORK/'resources.zip'])
sources=list((SRC/'src').rglob('*.java'))+list((WORK/'generated').rglob('*.java'))
run([JAVA/'javac.exe','-encoding','UTF-8','-source','8','-target','8','-classpath',JAR,'-d',WORK/'classes',*sources])
run([JAVA/'java.exe','-cp',BT/'lib/d8.jar','com.android.tools.r8.D8','--lib',JAR,'--min-api','28','--output',WORK/'dex',*list((WORK/'classes').rglob('*.class'))])
unsigned=WORK/'unsigned.apk';shutil.copy2(WORK/'resources.apk',unsigned)
with zipfile.ZipFile(unsigned,'a',zipfile.ZIP_DEFLATED)as z:
 for p in (WORK/'dex').glob('*.dex'):z.write(p,p.name)
run([BT/'zipalign.exe','-f','-p','4',unsigned,WORK/'aligned.apk'])
private=ROOT/'var/private/android-signing';private.mkdir(parents=True,exist_ok=True)
secret=private/'password.txt';key=private/'local-demo.p12'
if not key.exists():
 if not secret.exists():secret.write_text(secrets.token_urlsafe(32),encoding='utf-8')
 run([JAVA/'keytool.exe','-genkeypair','-keystore',key,'-storetype','PKCS12','-alias','zhenxing-local',
  '-keyalg','RSA','-keysize','3072','-validity','3650','-dname','CN=Zhenxing Agent Local, O=Zhenxing',
  '-storepass:file',secret,'-keypass:file',secret])
apk=OUT/'圳行Agent-本地连接版-v0.1.1.apk'
run([JAVA/'java.exe','-jar',BT/'lib/apksigner.jar','sign','--ks',key,'--ks-key-alias','zhenxing-local',
 '--ks-pass','file:'+str(secret),'--out',apk,WORK/'aligned.apk'])
verification=run([JAVA/'java.exe','-jar',BT/'lib/apksigner.jar','verify','--verbose','--print-certs',apk])
badging=run([BT/'aapt2.exe','dump','badging',apk])
run([BT/'zipalign.exe','-c','-v','4',apk])
(BUILD/'apk-signature.txt').write_text(verification,encoding='utf-8');(BUILD/'apk-badging.txt').write_text(badging,encoding='utf-8')
proof={'apk':apk.name,'bytes':apk.stat().st_size,'sha256':hashlib.sha256(apk.read_bytes()).hexdigest(),
 'package':'com.zhenxing.agent.local','min_android_api':28,'target_api':35,'signature_verified':True,
 'zip_alignment_verified':True,'frontend_embedded':True,'frontend_files':sum(p.is_file() for p in (SRC/'assets/web').rglob('*')),
 'backend_embedded':False,'private_signing_key_in_package':False}
(OUT/'构建信息.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(proof,ensure_ascii=False))
