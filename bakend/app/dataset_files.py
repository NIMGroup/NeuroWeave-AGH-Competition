"""Read-only dataset tools: roots come from registered data, never model arguments."""
from pathlib import Path

from .filesystem_reader import FilesystemReader
from .workspace_paths import WorkspacePaths, within


def protect_datasets(store, candidate: Path):
    candidate = candidate.resolve()
    for dataset in store.list_datasets():
        source = Path(dataset.absolute_path).resolve()
        if within(candidate, source) or within(source, candidate):
            raise PermissionError(f"数据集目录受只读保护，请选择与其分开的输出目录：{source}")


class DatasetReader(FilesystemReader):
    def __init__(self, root: Path):
        self._root = root
        self.paths = WorkspacePaths(root, allowed_roots=(root,))

    def root(self, workspace_id=None, root_path=None):
        if workspace_id or root_path:
            raise PermissionError("数据集读取范围不能由调用方改写")
        if self._root.resolve() != self._root:
            raise PermissionError("数据集目录已被重定向，请重新登记")
        return self.paths.browse_root(str(self._root))

    def inspect(self, *args, **kwargs):
        result = super().inspect(*args, **kwargs)
        if result.get('exists'):
            result.update(os_writable=result['writable'], writable=False, management='read_only_dataset')
        return result


class DatasetFiles:
    def __init__(self, datasets):
        self.datasets = datasets

    def reader(self, dataset_id: str) -> DatasetReader:
        from .data_auth import principal
        principal.get().check('dataset', dataset_id)
        dataset = self.datasets.get(dataset_id)
        _, resolved, _ = self.datasets._resolve(dataset.root_id, dataset.relative_path)
        if resolved != Path(dataset.absolute_path):
            raise PermissionError("已登记的数据集路径发生变化，请重新选择数据集")
        return DatasetReader(resolved)
