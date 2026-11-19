"""Build the companion with the installed Android SDK; no Gradle dependencies."""
import hashlib,json,os,re,secrets,shutil,subprocess,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def sdk_root():
    candidates=[os.environ.get('ANDROID_HOME'),os.environ.get('ANDROID_SDK_ROOT'),
               str(Path(os.environ.get('USERPROFILE') or Path.home())/'AppData/Local/Android/Sdk'),
               str(Path(os.environ.get('LOCALAPPDATA') or '')/'Android/Sdk')]
    # WorkBuddy and some launchers redirect USERPROFILE; the real SDK may live
    # under the Windows user profile, so look there before giving up.
    candidates += [str(p) for p in Path('C:/Users').glob('*/AppData/Local/Android/Sdk')]
    for value in candidates:
        if value and Path(value).is_dir() and any(Path(value).glob('build-tools/*')):return Path(value)
    raise SystemExit('未找到 Android SDK，请设置 ANDROID_HOME')


SDK=sdk_root()
BUILD=ROOT/'.local/android-build'
VERSION=re.search(r'android:versionName="([^"]+)"',(ROOT/'mobile/AndroidManifest.xml').read_text(encoding='utf-8')).group(1)


def newest(pattern):
    found=sorted(SDK.glob(pattern))
    if not found:raise SystemExit('缺少 Android SDK 组件：'+pattern)
    return found[-1]


TOOLS=newest('build-tools/*')
ANDROID=newest('platforms/android-*/android.jar')

def run(args):
    p=subprocess.run([str(v) for v in args],capture_output=True,text=True,encoding='utf-8',errors='replace')
    if p.returncode:raise RuntimeError(p.stdout+p.stderr)
    if p.stderr:print(p.stderr[-1200:])

def build():
    for folder in ('gen','classes','dex'): (BUILD/folder).mkdir(parents=True,exist_ok=True)
    run([TOOLS/'aapt2.exe','link','-I',ANDROID,'--manifest',ROOT/'mobile/AndroidManifest.xml','-o',BUILD/'resources.apk','-A',ROOT/'mobile/assets','--java',BUILD/'gen','--min-sdk-version','29','--target-sdk-version','36'])
    sources=sorted((ROOT/'mobile/src').rglob('*.java'))+sorted((BUILD/'gen').rglob('*.java'))
    argfile=BUILD/'javac.args'
    def quoted(v):return '"'+str(v).replace('\\','/')+'"'
    argfile.write_text('\n'.join(['-encoding','UTF-8','--release','11','-Xlint:-options','-classpath',quoted(ANDROID),'-d',quoted(BUILD/'classes'),*[quoted(p) for p in sources]]),encoding='utf-8')
    run(['javac','@'+str(argfile)])
    run([TOOLS/'d8.bat','--lib',ANDROID,'--min-api','29','--output',BUILD/'dex',*sorted((BUILD/'classes').rglob('*.class'))])
    unsigned=BUILD/'unsigned.apk';shutil.copy2(BUILD/'resources.apk',unsigned)
    with zipfile.ZipFile(unsigned,'a',compression=zipfile.ZIP_DEFLATED) as archive:
        for dex in (BUILD/'dex').glob('*.dex'):archive.write(dex,dex.name)
    run([TOOLS/'zipalign.exe','-f','4',unsigned,BUILD/'aligned.apk'])
    key=ROOT/'.local/android-signing-key.jks';password=ROOT/'.local/android-keypass.txt'
    if not key.exists():
        password.write_text(secrets.token_urlsafe(32),encoding='utf-8')
        run(['keytool','-genkeypair','-keystore',key,'-storepass:file',password,'-keypass:file',password,'-alias','videodesk','-keyalg','RSA','-keysize','3072','-validity','10000','-dname','CN=VideoDesk Local Companion'])
    apk=ROOT/'mobile/VideoDesk-Mobile.apk'
    run([TOOLS/'apksigner.bat','sign','--ks',key,'--ks-key-alias','videodesk','--ks-pass','file:'+str(password),'--out',apk,BUILD/'aligned.apk'])
    run([TOOLS/'apksigner.bat','verify','--verbose',apk])
    release=ROOT/'release'/('VideoDesk-Android-'+VERSION+'.apk');shutil.copy2(apk,release)
    digest=hashlib.sha256(apk.read_bytes()).hexdigest();release.with_suffix('.apk.sha256').write_text(digest+'  '+release.name+'\n',encoding='ascii')
    print(json.dumps({'file':release.name,'bytes':release.stat().st_size,'sha256':digest}))

if __name__=='__main__':build()
