"""Read, write, edit and execute within an explicitly supplied task workspace."""
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.runtime.registry import ToolSpec, execute
from .python_runner import execute_code


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadFile(Input):
    path: str
    start_line: int = Field(default=0, ge=0, description="从0起的行号")
    line_count: int = Field(default=160, ge=1, le=240)
    character_offset: int = Field(default=0, ge=0, description="同一选定行范围内的字符偏移，用于续读超长单行")


class WriteFile(Input):
    path: str
    text: str = Field(max_length=500000)


class EditFile(Input):
    path: str
    old_text: str = Field(min_length=1, max_length=500000)
    new_text: str = Field(max_length=500000)


class ExecutePython(Input):
    code: str | None = Field(default=None, max_length=30000)
    script: str | None = None
    arguments: list[str] = Field(default_factory=list, max_length=80)

    @model_validator(mode="after")
    def one_mode(self):
        if bool(self.code and self.code.strip()) == bool(self.script):
            raise ValueError("code 与 script 必须且只能提供一个")
        if self.code and self.arguments:
            raise ValueError("代码片段不接受脚本参数")
        return self


class FileWorkspace:
    def __init__(self, folder, resources, *, before_write=None, run_script=None):
        self.folder = Path(folder).resolve()
        self.resources = {key: Path(path).resolve() for key, path in resources.items()}
        self.before_write = before_write
        self.run_script = run_script

    def resolve(self, path, *, write=False, copy=False):
        value = str(path).replace("\\", "/")
        root = self.folder
        if value == "@project" or value.startswith("@project/"):
            value = value.removeprefix("@project").lstrip("/")
        for prefix, resource in self.resources.items():
            if value == prefix or value.startswith(prefix + "/"):
                if write:
                    raise ValueError("Skill 资源只读")
                root, value = resource, value[len(prefix):].lstrip("/")
                break
        target = (root / value).resolve()
        if not write and target.is_absolute():
            roots = [root, *self.resources.values()]
        else:
            roots = [self.folder]
        if not any(target.is_relative_to(allowed) for allowed in roots):
            raise ValueError("文件不属于当前任务或已安装的 Skill")
        if any(part.startswith(".") for part in target.relative_to(next(
                allowed for allowed in roots if target.is_relative_to(allowed))).parts):
            raise ValueError("不能访问隐藏文件或后台配置")
        if write:
            allowed_suffixes = {".md", ".txt", ".json", ".csv", ".svg", ".xml"}
            if copy:
                allowed_suffixes |= {".png", ".jpg", ".jpeg"}
            if target.suffix.lower() not in allowed_suffixes:
                raise ValueError("写入仅支持任务文本和设计文件，不能写入程序")
            if self.before_write:
                self.before_write(target.relative_to(self.folder).as_posix())
        return target

    def read_file(self, path, start_line=0, line_count=160, character_offset=0):
        target = self.resolve(path)
        if target.is_dir():
            return {"status": "success", "data": {"path": path, "entries": sorted(
                item.name + ('/' if item.is_dir() else '') for item in target.iterdir()
                if not item.name.startswith('.'))}}
        if not target.is_file():
            names = sorted(item.name for item in target.parent.iterdir()
                           if item.is_file() and not item.name.startswith('.')) if target.parent.is_dir() else []
            raise ValueError("文件不存在：%s。同目录实际文件：%s。请根据索引或实际文件名读取，不要猜测路径。" %
                             (path, '、'.join(names[:40]) or '无'))
        if target.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("文件超过20MiB；用户文档请先通过材料上传解析")
        if target.suffix.lower() not in {".md", ".txt", ".json", ".csv", ".svg", ".xml", ".py", ".yaml", ".yml"}:
            raise ValueError("只读取文本；PDF、Word、表格请读取上传服务已解析的任务材料")
        lines = target.read_text(encoding="utf-8-sig").splitlines()
        text = "\n".join(lines[start_line:start_line + line_count])
        if character_offset > len(text):
            raise ValueError("字符偏移超出选定内容")
        end = min(len(text), character_offset + 40000)
        return {"status": "success", "data": {
            "path": path, "text": text[character_offset:end], "total_lines": len(lines),
            "next_character_offset": end if end < len(text) else None}}

    def write_file(self, path, text):
        target = self.resolve(path, write=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return {"status": "success", "data": {"path": path, "written": len(text)}}

    def edit_file(self, path, old_text, new_text):
        target = self.resolve(path, write=True)
        if not target.is_file() or target.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("待修改文件不存在或过大")
        text = target.read_text(encoding="utf-8-sig")
        if text.count(old_text) != 1:
            raise ValueError("修改片段必须恰好匹配一处；请先读取当前文件")
        return self.write_file(path, text.replace(old_text, new_text, 1))

    def execute_python(self, code=None, script=None, arguments=None):
        if script:
            if self.run_script is None:
                raise ValueError("当前任务未授权安装脚本执行")
            return self.run_script(script, arguments or [])
        return execute_code(code, self)

    def tools(self):
        return [ToolSpec("read_file", "读取当前任务或已安装Skill的文本；目录路径返回真实文件名。支持@project/和@skill/路径，长文件按行续读。", ReadFile, self.read_file),
                ToolSpec("write_file", "写入当前任务的文本、设计规格、SVG等文件；不能改写Skill、来源或程序。", WriteFile, self.write_file),
                ToolSpec("edit_file", "将已读取文件中唯一匹配的片段替换，未匹配或多处匹配时不修改。", EditFile, self.edit_file),
                ToolSpec("execute_python", "执行受限Python代码，或运行当前任务允许的已安装Skill脚本。代码可用math/json/csv/statistics及read_text/write_text/copy_resource；不能访问系统、网络或导入其他模块。", ExecutePython, self.execute_python)]

    def execute(self, name, arguments):
        spec = next((tool for tool in self.tools() if tool.name == name), None)
        if spec is None:
            return {"status": "error", "error": {"code": "unknown_tool", "detail": "当前任务没有开放此工具"}}
        return execute(spec, arguments)
