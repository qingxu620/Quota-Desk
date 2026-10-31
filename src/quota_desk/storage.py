"""Atomic JSON storage with Windows user-bound encryption for account credentials."""
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile


class CredentialError(ValueError):
    pass


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


class DpapiProtector:
    """DPAPI current-user protection. It is not an encrypted portable backup format."""
    def _crypt(self, payload, decrypt=False):
        if os.name != "nt":
            raise CredentialError("真实账号仅支持 Windows；其他系统请使用 --demo。")
        raw = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
        source = _Blob(len(payload), raw)
        result = _Blob()
        dll = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        if decrypt:
            fn = dll.CryptUnprotectData
            fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                           ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
            args = (ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
        else:
            fn = dll.CryptProtectData
            fn.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                           ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
            args = (ctypes.byref(source), "Quota Desk account", None, None, None, 1, ctypes.byref(result))
        fn.restype = wintypes.BOOL
        if not fn(*args):
            raise CredentialError("无法保护或解密本地账号；请使用原 Windows 用户重新导入。")
        try:
            return ctypes.string_at(result.data, result.size)
        finally:
            kernel.LocalFree(result.data)

    def protect(self, payload):
        return base64.b64encode(self._crypt(payload)).decode("ascii")

    def unprotect(self, value):
        return self._crypt(base64.b64decode(value), decrypt=True)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class AccountStore:
    def __init__(self, home, protector=None):
        self.home = Path(home)
        self.protector = protector or DpapiProtector()

    def directory(self, provider):
        if provider not in ("agy", "codex"):
            raise ValueError("Unknown provider")
        return self.home / "accounts" / provider

    def slots(self, provider):
        return tuple(sorted((p.stem[3:] for p in self.directory(provider).glob("acc*.json")
                             if p.stem[3:].isdigit() and int(p.stem[3:]) > 0), key=int))

    def save_path(self, path, data):
        data = dict(data)
        key = "cred" if "cred" in data else "auth" if "auth" in data else None
        if key:
            secret = data.pop(key)
            data["encrypted_auth"] = {"scheme": "windows-dpapi-v1", "field": key,
                                      "value": self.protector.protect(json.dumps(secret).encode("utf-8"))}
        write_json(path, data)

    def load(self, provider, slot):
        if not str(slot).isdigit() or int(slot) <= 0:
            raise ValueError("账号编号必须为正整数。")
        path = self.directory(provider) / f"acc{slot}.json"
        return self.load_path(path)

    def load_path(self, path):
        path = Path(path)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            encrypted = data.pop("encrypted_auth", None)
            if encrypted:
                if encrypted.get("scheme") != "windows-dpapi-v1" or encrypted.get("field") not in ("cred", "auth"):
                    raise CredentialError("不支持的凭证格式；请重新导入账号。")
                data[encrypted["field"]] = json.loads(self.protector.unprotect(encrypted["value"]))
            return data
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise CredentialError(f"无法读取本地凭证文件 {path.name}；请重新导入。") from exc
