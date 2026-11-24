from pathlib import Path
import sys
import importlib.util
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT=Path(SPECPATH)
sys.path=[str(ROOT/'src'),str(ROOT)]+[p for p in sys.path if not (Path(p)/'lh2dt').is_dir()]
datas=[(str(ROOT/'authoring_ui/static'),'authoring_ui/static'),
       (str(ROOT/'configs'),'configs')]
datas += [(str(ROOT/'LICENSE'),'.'), (str(ROOT/'THIRD_PARTY_NOTICES.md'),'.'), (str(ROOT/'third_party_licenses'),'third_party_licenses')]
datas+=collect_data_files('CoolProp')
binaries=[(str(Path(sys.prefix)/'Library/bin'/name),'.') for name in
          ('ffi.dll','sqlite3.dll','tk86t.dll','tcl86t.dll')
          if (Path(sys.prefix)/'Library/bin'/name).is_file()]
if (Path(sys.prefix)/'python3.dll').is_file():
    binaries.append((str(Path(sys.prefix)/'python3.dll'),'.'))
coolprop_dir=Path(importlib.util.find_spec('CoolProp').origin).parent
binaries += [(str(p),'coolprop.libs') for p in (coolprop_dir.parent/'coolprop.libs').glob('*.dll')]
a=Analysis([str(ROOT/'launch_simulator.py')],pathex=[str(ROOT),str(ROOT/'src')],
           binaries=binaries,datas=datas,hiddenimports=collect_submodules('lh2dt'),
           hookspath=[],hooksconfig={},runtime_hooks=[],
           excludes=['pytest','IPython','matplotlib','PySide6','PyQt6','openpyxl'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='LH2ProcessSimulator',
        debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False,
        disable_windowed_traceback=False)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='LH2ProcessSimulator')
