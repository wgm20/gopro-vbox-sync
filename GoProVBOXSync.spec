# Build with scripts/build_release.py so notices and version resources are ready.
a = Analysis(['launch.pyw'], pathex=[], binaries=[],
             datas=[('goprovbox/assets', 'goprovbox/assets')],
             hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['pytest', 'unittest', 'setuptools'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='GoProVBOXSync',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False,
          icon='goprovbox/assets/icon.ico', version='build/version-info.txt')
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='GoProVBOXSync')
