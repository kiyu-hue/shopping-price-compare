import os, sys, subprocess
from pathlib import Path

base=Path(__file__).resolve().parent
if sys.platform != 'win32':
    raise SystemExit('Windows 전용 빌드입니다. Windows PC 또는 GitHub Actions windows-latest에서 실행해 주세요.')
root=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))
browsers=base/'_playwright_browsers'
env=os.environ.copy()
env['PLAYWRIGHT_BROWSERS_PATH']=str(browsers)
subprocess.check_call([sys.executable,'-m','playwright','install','chromium'],env=env)
if not browsers.is_dir() or not list(browsers.glob('chromium*')):
    raise SystemExit('Chromium 브라우저를 다운로드하지 못했습니다.')
args=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed','--onedir','--name','가격비교기',
      '--collect-all','playwright','--hidden-import','musinsa_dashboard','--hidden-import','comparison_dashboard','--hidden-import','kream_quotes','--hidden-import','auto_discovery','--add-data',f'{browsers}{os.pathsep}playwright_browsers',
      str(base/'price_compare.py')]
subprocess.check_call(args,cwd=base)
print("OUTPUT: dist")
