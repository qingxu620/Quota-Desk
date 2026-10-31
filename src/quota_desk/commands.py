"""Structured helper commands; the UI never constructs shell command text."""
import subprocess


def helper_command(backend, *args):
    command = [backend.PYTHONW_EXE, "-m", "quota_desk", "--data-dir", str(backend.SETTINGS.home)]
    if backend.DEMO:
        command.append("--demo")
    return command + list(args)


def launch_operation(backend, provider, slot, bind=False):
    return subprocess.Popen(helper_command(backend, "account", "bind" if bind else "switch", provider, str(slot)),
                            creationflags=backend.CREATE_NO_WINDOW, close_fds=True)
