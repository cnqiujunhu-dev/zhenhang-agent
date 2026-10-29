"""Download only official SDK build components; keep tools outside source/delivery."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib,json,urllib.request,zipfile

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'var/android-build/toolchain'
PACKAGES=[('platform','platform-35_r02.zip','0bb560a90a7a2cbd0dd8348224d518b638fe7949'),
 ('build-tools','build-tools_r35_windows.zip','af059bb67cf7786f45ee0db85e2d24985df1b4b6'),
 ('platform-tools','platform-tools_r37.0.1-win.zip','e03e78b1d80b396f1c3358e31251cb31740e1110')]
def fetch(row):
 name,filename,expected=row;folder=DEST/name;archive=DEST/filename
 if not archive.exists():
  with urllib.request.urlopen('https://dl.google.com/android/repository/'+filename,timeout=90)as src,archive.open('wb')as out:
   while block:=src.read(1024*1024):out.write(block)
 assert hashlib.sha1(archive.read_bytes()).hexdigest()==expected,filename
 folder.mkdir(parents=True,exist_ok=True)
 with zipfile.ZipFile(archive)as z:
  assert all((folder/n).resolve().is_relative_to(folder.resolve())for n in z.namelist())
  z.extractall(folder)
 return {'component':name,'source':'https://dl.google.com/android/repository/'+filename,'sha1':expected,'bytes':archive.stat().st_size}
if __name__=='__main__':
 DEST.mkdir(parents=True,exist_ok=True)
 with ThreadPoolExecutor(max_workers=3)as pool:records=list(pool.map(fetch,PACKAGES))
 (DEST/'provenance.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
 print('OFFICIAL_ANDROID_BUILD_TOOLS_READY',flush=True)
