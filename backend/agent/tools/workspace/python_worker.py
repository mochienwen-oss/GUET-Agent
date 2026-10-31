"""Restricted Python language worker; no general imports, OS or file handles."""
import ast
import contextlib
import csv
import io
import json
import math
from pathlib import Path
import statistics
import sys
from types import SimpleNamespace


class Output(io.StringIO):
    def write(self, value):
        if self.tell() + len(value) > 40000:
            raise ValueError("Python输出超过40,000字符")
        return super().write(value)


def main(request):
    tree = ast.parse(request["code"], mode="exec")
    if len(list(ast.walk(tree))) > 10000:
        raise ValueError("代码过长")
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.Await, ast.With, ast.AsyncWith, ast.Global, ast.Nonlocal)):
            raise ValueError("当前执行器不支持此语言结构")
        if isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr in {"mro", "format", "format_map"}):
            raise ValueError("不能访问反射或内部属性")
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise ValueError("不能访问内部名称")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list:
            raise ValueError("不允许装饰器")
    modules = {
        "math": SimpleNamespace(**{name: getattr(math, name) for name in dir(math) if not name.startswith("_")}),
        "json": SimpleNamespace(loads=json.loads, dumps=json.dumps),
        "csv": SimpleNamespace(reader=csv.reader, writer=csv.writer),
        "statistics": SimpleNamespace(**{name: getattr(statistics, name) for name in
            ("mean", "median", "mode", "stdev", "pstdev", "variance", "pvariance")}),
    }
    def import_module(name, globals=None, locals=None, fromlist=(), level=0):
        if level or name not in modules:
            raise ValueError("只允许math/json/csv/statistics")
        return modules[name]
    root = Path(request["root"]).resolve()
    resources = {key: Path(value).resolve() for key, value in request["resources"].items()}
    writes, copies = {}, {}
    def read_text(path):
        if path in writes:
            return writes[path]
        value, base = str(path).replace("\\", "/"), root
        if value.startswith("@project/"):
            value = value[len("@project/"):]
        for prefix, folder in resources.items():
            if value.startswith(prefix + "/"):
                base, value = folder, value[len(prefix) + 1:]
                break
        target = (base / value).resolve()
        if not target.is_relative_to(base) or any(p.startswith(".") for p in target.relative_to(base).parts):
            raise ValueError("文件超出授权目录")
        if target.suffix.lower() not in {".md", ".txt", ".json", ".csv", ".svg", ".xml", ".py"} or target.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("只读取2MiB以内的任务文本")
        return target.read_text(encoding="utf-8-sig")
    def write_text(path, text):
        if not isinstance(path, str) or not isinstance(text, str) or len(text) > 500000:
            raise ValueError("写入参数无效或过大")
        if sum(map(len, writes.values())) + len(text) > 2000000:
            raise ValueError("单次执行写入超过2,000,000字符")
        writes[path] = text
    def copy_resource(source, destination):
        if not isinstance(source, str) or not isinstance(destination, str) or len(copies) >= 20:
            raise ValueError("复制参数无效或数量过多")
        copies[destination] = source
    names = ("abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "int", "isinstance",
             "len", "list", "map", "max", "min", "next", "print", "range", "reversed", "round",
             "set", "slice", "sorted", "str", "sum", "tuple", "zip", "Exception", "ValueError", "TypeError")
    import builtins
    safe = {name: getattr(builtins, name) for name in names}
    safe["__import__"] = import_module
    namespace = {"__builtins__": safe, **modules, "read_text": read_text,
                 "write_text": write_text, "copy_resource": copy_resource, "StringIO": io.StringIO}
    output = Output()
    with contextlib.redirect_stdout(output):
        exec(compile(tree, "<task-python>", "exec"), namespace, namespace)
    return {"status": "success", "data": {"output": output.getvalue()}, "writes": writes, "copies": copies}


if __name__ == "__main__":
    try:
        # Windows memory is bounded by the parent Job Object.
        if sys.platform != "win32":
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        # 显式按 utf-8 字节写出：即使解释器编码被环境改回 cp936，父进程也能正确解码。
        payload = json.dumps(main(json.load(sys.stdin)), ensure_ascii=False)
        sys.stdout.buffer.write(payload.encode("utf-8"))
        sys.stdout.buffer.flush()
    except Exception as error:
        sys.stderr.buffer.write(str(error)[:2000].encode("utf-8", "replace"))
        sys.stderr.buffer.flush()
        sys.exit(1)
