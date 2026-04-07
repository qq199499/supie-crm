import subprocess
import sys
from pathlib import Path

import servicemanager
import win32event
import win32service
import win32serviceutil


class SupieCRMService(win32serviceutil.ServiceFramework):
    _svc_name_ = "supie_crm"
    _svc_display_name_ = "项目过程管理系统服务"
    _svc_description_ = "项目过程管理系统服务"
    _exe_name_ = sys.executable
    _exe_args_ = f'"{Path(__file__).resolve()}"'

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.child = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        if self.child and self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.child.kill()
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self):
        root_dir = Path(__file__).resolve().parents[1]
        python_exe = root_dir / ".venv" / "Scripts" / "python.exe"
        if not python_exe.exists():
            python_exe = Path(sys.executable)
        log_dir = root_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "service.log"
        cmd = [str(python_exe), str(root_dir / "ops" / "service_runner.py")]
        self.child = subprocess.Popen(
            cmd,
            cwd=str(root_dir),
            stdout=open(log_file, "a", encoding="utf-8"),
            stderr=open(log_file, "a", encoding="utf-8"),
        )
        servicemanager.LogInfoMsg(f"{self._svc_name_} started: {' '.join(cmd)}")
        self.ReportServiceStatus(win32service.SERVICE_RUNNING)

        try:
            while True:
                stop_requested = win32event.WaitForSingleObject(self.stop_event, 3000)
                if stop_requested == win32event.WAIT_OBJECT_0:
                    break
                if self.child.poll() is not None:
                    servicemanager.LogErrorMsg(
                        f"{self._svc_name_} child exited unexpectedly with code {self.child.returncode}"
                    )
                    break
        finally:
            self.ReportServiceStatus(win32service.SERVICE_STOPPED)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(SupieCRMService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(SupieCRMService)
