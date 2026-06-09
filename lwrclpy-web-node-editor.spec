# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('lwrclpy_web_node_editor/static', 'lwrclpy_web_node_editor/static'), ('lwrclpy_web_node_editor/node_worker.py', 'lwrclpy_web_node_editor'), ('lwrclpy_web_node_editor/video_dds_worker.py', 'lwrclpy_web_node_editor'), ('lwrclpy_web_node_editor/dds_tap_worker.py', 'lwrclpy_web_node_editor'), ('lwrclpy_web_node_editor/builtin_source_worker.py', 'lwrclpy_web_node_editor'), ('scripts/install_lwrclpy.py', 'scripts')]
binaries = [('/Users/tatsuyai/repos/Image-Processing-Node-Editor-ROS2/lwrclpy_web_node_editor/.venv/bin/uv', '.')]
hiddenimports = ['cv2', 'webview.platforms.cocoa', 'objc', 'Cocoa', 'WebKit']
tmp_ret = collect_all('lwrclpy')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('rclpy')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('fastdds')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='lwrclpy-web-node-editor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='lwrclpy-web-node-editor',
)
