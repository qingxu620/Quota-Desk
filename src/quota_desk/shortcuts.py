"""Create optional desktop shortcuts using the active Python installation."""
import base64
import ctypes
import json
import os
import subprocess

from .commands import helper_command
from .windows import powershell


def install(backend):
    if os.name != "nt":
        raise ValueError("桌面快捷方式仅支持 Windows。")
    folder = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, folder) != 0:
        raise OSError("无法定位桌面目录。")
    commands = []
    for simple in (False, True):
        command = helper_command(backend, *(('--simple',) if simple else ()))
        commands.append({"name": "Quota Desk（朴素版）" if simple else "Quota Desk",
                         "target": command[0], "args": subprocess.list2cmdline(command[1:]),
                         "icon": backend.SIMPLE_ICON_PATH if simple else backend.DASHBOARD_ICON_PATH,
                         "folder": folder.value})
    blob = base64.b64encode(json.dumps(commands, ensure_ascii=False).encode()).decode()
    powershell("$data=ConvertFrom-Json([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + blob + "'))); "
               "$shell=New-Object -ComObject WScript.Shell; foreach($item in $data){ "
               "$s=$shell.CreateShortcut((Join-Path $item.folder ($item.name+'.lnk'))); "
               "$s.TargetPath=$item.target; $s.Arguments=$item.args; $s.IconLocation=$item.icon; $s.Save() }")
