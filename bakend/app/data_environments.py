"""Administrator-selected immutable images and bounded resource presets."""
import json
import os
import re
from pathlib import Path

from .fact_memory import digest


RESOURCE_PROFILES = {
    'small': {'timeout_seconds': 60, 'cpus': 1, 'memory_mb': 768, 'output_mb': 512, 'tmp_mb': 64, 'pids': 64},
    'standard': {'timeout_seconds': 300, 'cpus': 2, 'memory_mb': 2048, 'output_mb': 1024, 'tmp_mb': 256, 'pids': 128},
    'extended': {'timeout_seconds': 900, 'cpus': 2, 'memory_mb': 4096, 'output_mb': 2048, 'tmp_mb': 512, 'pids': 128},
}


class DataEnvironments:
    def __init__(self):
        path = os.getenv('NEUROWEAVE_DATA_PYTHON_ENVIRONMENTS_FILE', '')
        raw = os.getenv('NEUROWEAVE_DATA_PYTHON_ENVIRONMENTS', '{}')
        if path:
            source = Path(path)
            if source.stat().st_size > 65536:
                raise ValueError('脚本环境配置过大')
            raw = source.read_text(encoding='utf8')
        self.configured = json.loads(raw)
        if not isinstance(self.configured, dict) or set(self.configured) - {'table', 'imaging'}:
            raise ValueError('只支持 table 与 imaging 环境配置')
        for name, item in self.configured.items():
            if (not isinstance(item, dict) or not re.fullmatch(r'sha256:[0-9a-f]{64}', item.get('image', ''))
                    or not isinstance(item.get('packages', {}), dict)):
                raise ValueError('第三方库环境必须使用固定镜像 ID 与依赖清单')
            if not all(isinstance(k, str) and isinstance(v, str) for k, v in item.get('packages', {}).items()):
                raise ValueError('依赖名称和版本必须是文本')

    def describe(self, name, resource_profile, stdlib_image):
        if resource_profile not in RESOURCE_PROFILES:
            raise ValueError('未知脚本资源配置')
        value = {'image': stdlib_image, 'packages': {}} if name == 'stdlib' else self.configured.get(name)
        if not value or not value.get('image'):
            raise ValueError('脚本环境未启用：' + name + '；请查询可用环境')
        return {'environment': name, 'image': value['image'], 'packages': dict(value.get('packages', {})),
                'lock_sha256': value.get('lock_sha256'), 'resource_profile': resource_profile,
                'resources': dict(RESOURCE_PROFILES[resource_profile])}

    def listing(self, stdlib_image):
        rows = [{'name': 'stdlib', 'available': bool(stdlib_image), 'packages': {}}]
        rows.extend({'name': name, 'available': name in self.configured,
                     'packages': self.configured.get(name, {}).get('packages', {})} for name in ('table', 'imaging'))
        return {'environments': rows, 'resource_profiles': RESOURCE_PROFILES,
                'execution_scope': 'data_script_sandbox', 'check': 'configuration_only',
                'configuration_version': digest([stdlib_image, self.configured, RESOURCE_PROFILES]),
                'notice': '库清单和资源配额仅适用于数据 Python 脚本沙箱；不能用于科学 Runner、Slurm 任务或判断 MRtrix 等软件可用。',
                'network': False, 'input_readonly': True, 'runtime_install': False}
