"""Reviewable data changes. Scope IDs are resolved by the server, never by code."""
from typing import Literal, Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator
from .data_selection import DataSelection
from .script_verification import ScriptCase, ManifestInvariants


class DataEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["copy", "rename", "write_text", "delete"]
    path: str = Field(min_length=1, max_length=4096)
    destination: str | None = Field(default=None, max_length=4096)
    content: str | None = Field(default=None, max_length=2_000_000)


ExecutionPolicy = Literal["preview", "output"]
OUTPUT_EXECUTION = "仅准备待运行方案；兼容旧参数 output，但不会自动执行。必须由用户在任务卡点击运行，覆盖、删除和数据集原地修改仍须单独确认。"


class DataPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str
    dataset_id: str | None = None
    workspace_id: str | None = None
    source_scope: Literal["dataset", "output"] = "dataset"
    mode: Literal["copy", "inplace"] = "copy"
    title: str = Field(default="数据整理", max_length=200)
    edits: list[DataEdit] = Field(min_length=1, max_length=10000)
    execution: ExecutionPolicy = Field(default="preview", description=OUTPUT_EXECUTION)


class NamingRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    match_on: Literal["filename", "stem"] = "filename"
    kind: Literal["image", "file"] = "image"
    exclude: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=30)
    priority: int = Field(default=0, ge=-100, le=100)


class OrganizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str
    dataset_id: str | None = None
    workspace_id: str | None = None
    source_scope: Literal["dataset", "output"] = "dataset"
    mode: Literal["copy", "inplace"] = "copy"
    title: str = Field(default="按规则批量整理", max_length=200)
    path: str | None = Field(default=None, max_length=4096)
    selection: DataSelection = Field(default_factory=DataSelection)
    rules: list[NamingRule] = Field(default_factory=list, max_length=100)
    base_operation_id: str | None = None
    replace_rules: bool = False
    remove_patterns: list[str] = Field(default_factory=list, max_length=100)
    exclude_paths: list[Annotated[str, Field(min_length=1, max_length=4096)]] = Field(default_factory=list, max_length=100)
    remove_exclusions: list[str] = Field(default_factory=list, max_length=100)
    duplicate_policy: Literal["number", "conflict"] | None = None
    unmatched_policy: Literal["skip", "preserve"] | None = Field(default=None, description="preserve 将未识别文件按原名复制，不猜测序列；skip 保持未匹配，完整覆盖要求下会阻止执行。")
    require_complete: bool = False
    execution: ExecutionPolicy = Field(default="preview", description=OUTPUT_EXECUTION)


class GeneratedDataPlan(DataPlanRequest):
    """Only server-scanned bounded inventories use this larger manifest limit."""
    edits: list[DataEdit] = Field(min_length=1, max_length=100000)


class DataConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]
    conversation_id: str
    manifest_hash: str
    allow_inplace: bool = False
    allow_delete: bool = False
    allow_overwrite: bool = False


class DataScriptConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]
    conversation_id: str
    script_plan_hash: str


class PythonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str
    dataset_id: str | None = None
    workspace_id: str | None = None
    source_scope: Literal["dataset", "output"] = "dataset"
    mode: Literal["analysis", "copy", "inplace"] = "analysis"
    result_kind: Literal['files', 'changes'] = Field(default='files', description='files 收集生成文件；changes 只输出 /output/changes.json，格式 {"edits":[{"action":"copy|rename|write_text|delete","path":"来源相对路径","destination":"目标相对路径"}]}，最多100000条/32MiB，不允许其他输出文件；报告打印到stdout。后端校验后显示改动预览，仍须再次点击运行才能修改文件，覆盖和删除须确认。')
    change_actions: list[Literal['copy', 'rename', 'write_text', 'delete']] = Field(default_factory=list, max_length=4, description='changes 模式必须事先声明允许的动作；输出不得扩大动作或选择范围。')
    selection: DataSelection = Field(default_factory=DataSelection, description='inplace 模式生成的改动必须在此范围；输入仍挂载当前来源目录只读。')
    code: str = Field(min_length=1, max_length=100000, description='容器输入根目录固定为 /input（只读），输出为 /output，工作目录为 /output。使用绝对输入路径；不提供 INPUT_DIR/OUTPUT_DIR 变量，不使用宿主路径。全目录任务应递归扫描并遵守 selection；文件结果写到 /output，analysis 只打印结果。不得安装库或访问网络。')
    title: str = Field(default="Python 数据调整", max_length=200)
    execution: ExecutionPolicy = Field(default="preview", description=OUTPUT_EXECUTION)
    environment: Literal['stdlib', 'table', 'imaging'] = Field(default='stdlib', description='使用已审核环境；先查询 list_data_environments 获得实际可用库。不能安装依赖。')
    resource_profile: Literal['small', 'standard', 'extended'] = Field(default='small', description='small 60秒/768MiB；standard 300秒/2GiB；extended 900秒/4GiB。输出额度分别512MiB/1GiB/2GiB。')
    background: bool = Field(default=False, description='保留旧请求兼容；无论取值，提交仅准备脚本，用户点击运行后才后台执行。')
    test_cases: list[ScriptCase] = Field(default_factory=list, max_length=16, description='将变换逻辑组织为可调用函数，通常用2至6项覆盖普通与必要边界；最多16项，不穷举。声明准确期望；只在用户点击运行后同一沙箱中校验，失败不生成可应用方案。不得执行函数来准备方案。')
    manifest_invariants: ManifestInvariants = Field(default_factory=ManifestInvariants, description='全量整理声明 cover_selected_files；需保持同名关联时声明扩展名组。后端独立核验实际清单，不能用打印日志替代。')

    @model_validator(mode='after')
    def result_contract(self):
        if self.result_kind == 'changes':
            if self.mode == 'analysis' or not self.change_actions:
                raise ValueError('生成可执行清单须使用 copy/inplace 并声明 change_actions；analysis 仅返回分析结果')
            if self.mode == 'copy' and set(self.change_actions) - {'copy', 'write_text'}:
                raise ValueError('copy 清单不能改名或删除来源文件')
        elif self.change_actions:
            raise ValueError('change_actions 仅用于 changes 结果')
        if self.result_kind != 'changes' and (self.manifest_invariants.cover_selected_files or self.manifest_invariants.preserve_stem_groups):
            raise ValueError('文件清单不变量仅用于 changes 结果；汇总分析用函数案例验证')
        return self


class NameTransform(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['replace', 'strip_prefix', 'strip_suffix']
    old: str = Field(min_length=1, max_length=200)
    new: str = Field(default='', max_length=200)


class TransformRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    conversation_id: str
    dataset_id: str | None = None
    workspace_id: str | None = None
    source_scope: Literal['dataset', 'output'] = 'output'
    mode: Literal['copy', 'inplace'] = 'inplace'
    title: str = Field(default='批量名称变换', max_length=200)
    selection: DataSelection = Field(default_factory=DataSelection)
    match_on: Literal['stem', 'filename'] = 'stem'
    transforms: list[NameTransform] = Field(min_length=1, max_length=20)
    execution: ExecutionPolicy = Field(default='preview', description=OUTPUT_EXECUTION)
