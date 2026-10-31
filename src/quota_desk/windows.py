"""Windows credential and client APIs, imported lazily for offline demo portability."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes as wt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def powershell(script, timeout=12):
    import base64
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                             base64.b64encode(script.encode("utf-16-le")).decode()],
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=timeout, creationflags=NO_WINDOW)
    if result.returncode:
        raise RuntimeError("无法读取 Windows 客户端进程信息。")
    return result.stdout


class _Credential(ctypes.Structure):
    _fields_ = [("Flags", wt.DWORD), ("Type", wt.DWORD), ("TargetName", wt.LPWSTR),
                ("Comment", wt.LPWSTR), ("LastWritten", wt.FILETIME),
                ("CredentialBlobSize", wt.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wt.DWORD), ("AttributeCount", wt.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wt.LPWSTR), ("UserName", wt.LPWSTR)]


class WindowsCredentials:
    def __init__(self, target="gemini:antigravity"):
        self.target = target

    def _api(self):
        if os.name != "nt":
            raise RuntimeError("客户端账号操作仅支持 Windows。")
        api = ctypes.WinDLL("advapi32", use_last_error=True)
        api.CredReadW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.POINTER(_Credential))]
        api.CredReadW.restype = wt.BOOL
        api.CredFree.argtypes = [ctypes.c_void_p]
        api.CredFree.restype = None
        api.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wt.DWORD]
        api.CredWriteW.restype = wt.BOOL
        api.CredDeleteW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD]
        api.CredDeleteW.restype = wt.BOOL
        return api

    def read(self):
        api = self._api()
        pointer = ctypes.POINTER(_Credential)()
        if not api.CredReadW(self.target, 1, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == 1168:
                return None
            raise OSError("无法读取 Windows 凭据管理器。")
        try:
            return json.loads(ctypes.string_at(pointer.contents.CredentialBlob,
                                               pointer.contents.CredentialBlobSize).decode("utf-8"))
        finally:
            api.CredFree(pointer)

    def write(self, auth):
        api = self._api()
        raw = json.dumps(auth, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        credential = _Credential()
        credential.Type, credential.TargetName, credential.Persist = 1, self.target, 2
        credential.CredentialBlob, credential.CredentialBlobSize = buffer, len(raw)
        credential.UserName = "antigravity"
        if not api.CredWriteW(ctypes.byref(credential), 0):
            raise OSError("无法写入 Windows 凭据管理器。")
        if self.read() != auth:
            raise OSError("账号写入读回不一致。")

    def clear(self):
        api = self._api()
        if not api.CredDeleteW(self.target, 1, 0) and ctypes.get_last_error() != 1168:
            raise OSError("无法清除当前登录状态。")


@contextmanager
def operation_lock(home, provider, timeout_ms=30_000):
    """Serialize refresh/import/switch operations across dashboard and helper processes."""
    if os.name != "nt":
        yield
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    kernel.CreateMutexW.restype = wt.HANDLE
    kernel.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel.WaitForSingleObject.restype = wt.DWORD
    kernel.ReleaseMutex.argtypes = [wt.HANDLE]
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    token = hashlib.sha256(str(Path(home).resolve()).casefold().encode()).hexdigest()[:16]
    handle = kernel.CreateMutexW(None, False, f"Local\\QuotaDesk-{token}-{provider}")
    if not handle:
        raise OSError("无法创建账号操作锁。")
    acquired = False
    try:
        acquired = kernel.WaitForSingleObject(handle, timeout_ms) in (0, 0x80)
        if not acquired:
            raise TimeoutError("此服务仍在刷新或切换，请稍后重试。")
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


class ClientController:
    def __init__(self, settings, provider):
        self.settings, self.provider = settings, provider

    def _matches(self, process):
        path = (process.get("ExecutablePath") or "").replace("/", "\\").casefold()
        if self.provider == "agy":
            return path == str(self.settings.antigravity_exe).replace("/", "\\").casefold()
        names = {n.casefold() for n in self.settings.codex_process_names}
        return (process.get("Name") or "").casefold() in names and "\\windowsapps\\openai.codex_" in path

    def preflight(self):
        if os.name != "nt":
            raise RuntimeError("客户端切换仅支持 Windows。")
        if self.provider == "agy" and not self.settings.antigravity_exe.is_file():
            raise FileNotFoundError("未找到 Antigravity；请在 config.toml 设置 antigravity_exe。")
        if self.provider == "codex" and self.settings.codex_launch[-1].startswith("shell:AppsFolder\\OpenAI.Codex_"):
            if not powershell("Get-AppxPackage -Name OpenAI.Codex | Select-Object -ExpandProperty PackageFamilyName").strip():
                raise FileNotFoundError("未找到 Codex 桌面应用；请安装客户端或配置 codex_launch。")

    def stop(self):
        self.preflight()
        raw = powershell("[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); Get-CimInstance Win32_Process | "
                         "Select-Object ProcessId,ParentProcessId,Name,ExecutablePath | ConvertTo-Json -Compress")
        processes = json.loads(raw or "[]")
        if isinstance(processes, dict):
            processes = [processes]
        roots = {p["ProcessId"] for p in processes if self._matches(p)}
        owned = set(roots)
        while True:
            children = {p["ProcessId"] for p in processes if p["ParentProcessId"] in owned}
            if children <= owned:
                break
            owned.update(children)
        user = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        user.EnumWindows.argtypes = [callback_type, wt.LPARAM]
        user.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        user.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        def close(hwnd, _param):
            pid = wt.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in roots:
                user.PostMessageW(hwnd, 0x10, 0, 0)  # WM_CLOSE, not TerminateProcess
            return True
        user.EnumWindows(callback_type(close), 0)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        kernel.OpenProcess.restype = wt.HANDLE
        kernel.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
        kernel.WaitForSingleObject.restype = wt.DWORD
        kernel.CloseHandle.argtypes = [wt.HANDLE]
        deadline = time.monotonic() + 10
        for pid in owned:
            handle = kernel.OpenProcess(0x00100000, False, pid)
            if not handle:
                if ctypes.get_last_error() == 87:
                    continue  # ERROR_INVALID_PARAMETER: the snapshot process has exited.
                raise OSError("无法确认客户端是否已退出，账号未更改。")
            try:
                remaining = max(0, int((deadline - time.monotonic()) * 1000))
                if kernel.WaitForSingleObject(handle, remaining) != 0:
                    raise RuntimeError("客户端尚未正常关闭，账号未更改。请先结束客户端中的任务后重试。")
            finally:
                kernel.CloseHandle(handle)
        return bool(roots)

    def start(self):
        self.preflight()
        command = [str(self.settings.antigravity_exe)] if self.provider == "agy" else self.settings.codex_launch
        subprocess.Popen(command, creationflags=NO_WINDOW, close_fds=True)
