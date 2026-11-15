# Windows one-file worker; no test seams or signing keys in this artifact.
a = Analysis(['account_maintenance_worker.py'], pathex=[], binaries=[], datas=[], hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=['pytest'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='maintenance-worker', debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
