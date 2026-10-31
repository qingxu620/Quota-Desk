"""Explicit account changes, with graceful client stop and recoverable login rollback."""
from pathlib import Path
import time

from .windows import ClientController, operation_lock


class AccountOperations:
    def __init__(self, backend, controller_factory=ClientController):
        self.backend, self.controller_factory = backend, controller_factory

    def run(self, provider, slot, bind=False, login_timeout=180):
        q = self.backend
        if q.DEMO:
            raise ValueError("演示模式不会切换、绑定或修改客户端账号。")
        sw = q.provider_switcher(provider)
        with operation_lock(q.SETTINGS.home, provider):
            sw.refresh_slots()
            target = sw.load_account_slot(str(slot))
            if not target:
                raise ValueError("账号不存在，请先添加账号。")
            controller = self.controller_factory(q.SETTINGS, provider)
            controller.preflight()
            sw.auto_save_current_auth()
            previous = sw.read_current()
            recovery = Path(q.SETTINGS.home) / "recovery" / f"{provider}.json"
            if not bind:
                auth = target.get(sw.key)
                if not sw.is_valid_auth_obj(auth):
                    raise ValueError("账号尚未绑定，请先登录绑定。")
                if sw.same_identity(previous, auth):
                    return
                self._refresh_target(sw, auth)
                sw.write_json_atomic(Path(sw.ACCOUNTS_DIR) / f"acc{slot}.json", target)
            controller.stop()  # Must succeed before touching the client's credential.
            if sw.is_valid_auth_obj(previous):
                q.STORE.save_path(recovery, {sw.key: previous})
            try:
                if bind:
                    sw.clear_current()
                    controller.start()
                    self._wait_for_login(sw, str(slot), login_timeout)
                else:
                    sw.write_current(auth)
                    controller.start()
            except Exception:
                # A failed login or restart must not leave the old login silently lost.
                controller.stop()
                if previous is not None:
                    sw.write_current(previous)
                else:
                    sw.clear_current()
                controller.start()
                if recovery.exists():
                    recovery.unlink()
                raise
            if recovery.exists():
                recovery.unlink()

    @staticmethod
    def _refresh_target(sw, auth):
        if sw.kind == "agy":
            expiry = sw.parse_iso_expiry(auth["token"].get("expiry", ""))
            if expiry - time.time() < 300 and not sw.refresh_google_oauth_token(auth):
                raise ValueError("该账号凭证已过期。请先在客户端重新登录并保存当前账号；或配置匹配的 OAuth 客户端信息。")
        else:
            expiry = sw.get_jwt_payload(auth["tokens"]["access_token"]).get("exp", 0)
            if expiry and expiry - time.time() < 300:
                success, _ = sw.refresh_openai_token(auth["tokens"])
                if not success:
                    raise ValueError("该账号登录状态需要更新；请在客户端重新登录后保存。")

    @staticmethod
    def _wait_for_login(sw, slot, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            auth = sw.read_current()
            if sw.is_valid_auth_obj(auth) and sw.metadata(auth)[1]:
                for other in sw.SLOT_ORDER:
                    if other != slot and sw.same_identity(auth, (sw.load_account_slot(other) or {}).get(sw.key)):
                        raise ValueError("刚才登录的是已添加账号，未创建重复账号。")
                sw.auto_save_current_auth(force_idx=slot)
                return
            time.sleep(.5)
        raise TimeoutError("等待登录超时，已尝试恢复原账号。")

    def recover(self, provider):
        q = self.backend
        if q.DEMO:
            raise ValueError("演示模式不会恢复客户端账号。")
        with operation_lock(q.SETTINGS.home, provider):
            sw = q.provider_switcher(provider)
            path = Path(q.SETTINGS.home) / "recovery" / f"{provider}.json"
            saved = q.STORE.load_path(path)
            if not saved or not sw.is_valid_auth_obj(saved.get(sw.key)):
                raise ValueError("没有可恢复的账号记录。")
            controller = self.controller_factory(q.SETTINGS, provider)
            controller.stop()
            sw.write_current(saved[sw.key])
            controller.start()
            path.unlink()
