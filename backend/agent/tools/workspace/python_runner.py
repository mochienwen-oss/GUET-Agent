"""Python snippets run in a separate process with an explicit capability set."""
import json
import os
from pathlib import Path
import subprocess
import sys

from agent.core.request_control import remaining_seconds, check_cancelled


def _windows_limit(process):
    if os.name != 'nt':
        return None
    import win32job
    # 第二个参数是作业对象名，pywin32 只接受 str；空串即匿名作业，不能传 None，
    # 也不能传固定名字（同名作业全局复用，会串到别的任务）。
    job = win32job.CreateJobObject(None, '')
    try:
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits['BasicLimitInformation']['LimitFlags'] |= (
            win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY | win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        limits['ProcessMemoryLimit'] = 512 * 1024 * 1024
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
        win32job.AssignProcessToJobObject(job, int(process._handle))
    except Exception as error:
        job.Close()
        raise OSError('无法设置Python执行进程的资源限制，未执行代码') from error
    return job.Close

def execute_code(code, workspace):
    # The worker never receives credentials, database access or project source.
    worker = Path(__file__).with_name("python_worker.py")
    request = {"code": code, "root": str(workspace.folder),
               "resources": {k: str(v) for k, v in workspace.resources.items()}}
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "LANG"}}
    environment["PYTHONIOENCODING"] = "utf-8"
    # -I 同时隐含 -E，环境变量里的编码设置会被忽略，中文输出会回落成 cp936，
    # 父进程按 utf-8 解码就会拿到 None。用命令行开关强制 UTF-8 模式才不受影响。
    process = subprocess.Popen([sys.executable, "-I", "-S", "-X", "utf8", str(worker)],
                               cwd=workspace.folder, env=environment,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace")
    release = None
    try:
        # Worker waits on stdin, so restrictions are installed before user code.
        release = _windows_limit(process)
        output, error = process.communicate(json.dumps(request), timeout=remaining_seconds(30))
    except BaseException:
        process.kill()
        process.communicate()
        raise
    finally:
        if release:
            release()
    check_cancelled()
    if process.returncode:
        return {"status": "error", "error": {"code": "python_failed", "detail": error[-2000:]}}
    result = json.loads(output)
    # Writes are staged by the worker and committed through the same C guard.
    writes, copies = result.pop("writes", {}), result.pop("copies", {})
    for path in writes:
        workspace.resolve(path, write=True)
    for path, source in copies.items():
        workspace.resolve(path, write=True, copy=True)
        origin = workspace.resolve(source)
        if origin.suffix.lower() not in {".svg", ".png", ".jpg", ".jpeg", ".md", ".json"} or not origin.is_file():
            raise ValueError("只能复制已安装Skill或任务中的设计资源")
    for path, content in writes.items():
        workspace.write_file(path, content)
    for path, source in copies.items():
        import shutil
        target = workspace.resolve(path, write=True, copy=True)
        origin = workspace.resolve(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)
    result.setdefault('data', {})['written_files'] = [*writes, *copies]
    return result
