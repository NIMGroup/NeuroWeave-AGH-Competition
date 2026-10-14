"""Functional navigation over the complete native library (never a routing gate).

IDs, categories and software facets are locale independent. Skills inherit the
functions of their referenced contracts; Pipelines inherit their actual steps.
No runtime or execution permission is inferred from a navigation category.
"""
from __future__ import annotations

from collections import Counter
import re

# Stable IDs are used in URLs and filters; display names may be translated.
GROUPS = {
    'datasets': ('数据集专属', 'Dataset-specific'),
    'data': ('数据准备', 'Data preparation'),
    'preprocessing': ('预处理与校正', 'Preprocessing and correction'),
    'registration': ('配准与空间变换', 'Registration and transforms'),
    'segmentation': ('分割与组织建模', 'Segmentation and tissue modelling'),
    'diffusion': ('扩散与纤维分析', 'Diffusion and tractography'),
    'structural': ('结构与表面分析', 'Structural and surface analysis'),
    'functional': ('功能影像分析', 'Functional imaging'),
    'electrophysiology': ('脑电与脑磁', 'EEG and MEG'),
    'statistics': ('统计与机器学习', 'Statistics and machine learning'),
    'quality': ('质控与可视化', 'Quality control and visualization'),
    'utilities': ('通用数据操作', 'General data operations'),
}
SUBGROUPS = {
    'cohorts': ('数据集适配与质控', 'Dataset adapters and quality control'),
    'datasets': ('数据集专属', 'Dataset-specific'),
    'conversion': ('格式转换', 'Format conversion'), 'organization': ('数据组织与检查', 'Organization and inspection'),
    'denoise': ('去噪与伪影校正', 'Denoising and artefact correction'), 'correction': ('信号与运动校正', 'Signal and motion correction'),
    'alignment': ('影像配准', 'Image registration'), 'transforms': ('应用与处理变换', 'Transform operations'),
    'tissue': ('组织分割与掩膜', 'Tissue segmentation and masks'),
    'response': ('响应函数与 FOD', 'Response functions and FOD'), 'tensor': ('张量与扩散指标', 'Tensors and diffusion metrics'),
    'tracking': ('纤维追踪与流线处理', 'Tracking and streamline operations'), 'connectome': ('连接组', 'Connectomes'),
    'fixel': ('Fixel 分析', 'Fixel analysis'), 'directions': ('梯度与方向处理', 'Gradients and directions'),
    'surface': ('表面与形态学', 'Surfaces and morphometry'), 'timeseries': ('时间序列与连接性', 'Time series and connectivity'),
    'signals': ('信号处理', 'Signal processing'), 'modelling': ('统计建模', 'Statistical modelling'),
    'reports': ('检查、报告与展示', 'Inspection, reports and display'), 'images': ('影像运算', 'Image operations'),
    'tables': ('表格与文件处理', 'Tables and files'), 'guidance': ('领域规划与指导', 'Domain planning and guidance'),
}


def method_category(method):
    """Classify by explicit command/port semantics, not software installation state."""
    identity = method.id.lower()
    text = identity + ' ' + ' '.join(method.output_types) + ' ' + method.label.lower()
    # Prefer reviewed command identities to broad software-family fallbacks.
    command=identity.rsplit('.',1)[-1]
    exact={
        'registration/transforms': {'convert_xfm','convertwarp','invwarp','mri_vol2vol','resampleimage'},
        'preprocessing/correction': {'n4biasfieldcorrection','mri_normalize','fslreorient2std'},
        'data/conversion': {'mri_convert','mris_convert','fslchfiletype'},
        'statistics/modelling': {'randomise','fsl_glm','fsl_regfilt','smoothest'},
        'quality/reports': {'mri_info','mri_segstats','fslcc','mrmetric','labeloverlapmeasures','labelgeometrymeasures','imageintensitystatistics','printheader'},
        'segmentation/tissue': {'atropos','mri_mask','mri_binarize','thresholdimage'},
        'utilities/images': {'fslmaths','fslroi','fslmerge','fslsplit','mri_concat','averageimages','extractslicefromimage','extractregionfromimage','multiplyimages','imagemath'},
        'functional/timeseries': {'fslmeants','fslpspec'},
    }
    for category,commands in exact.items():
        if command in commands:return tuple(category.split('/'))
    rules = [
        (r'connectom|connectivit.*matrix', ('diffusion', 'connectome')),
        (r'fixel|fixel_', ('diffusion', 'fixel')),
        (r'dwi2response|dwi2fod|response_function|fod_image|mtnormalise|sh2|shconv|amp2sh|amp2response|peaks2amp', ('diffusion', 'response')),
        (r'tck|tractogr|streamline', ('diffusion', 'tracking')),
        (r'dwi2tensor|tensor2|dtifit|dwi2adc|diffusion_tensor', ('diffusion', 'tensor')),
        (r'\.dir(gen|flip|merge|order|split|stat)|gradient', ('diffusion', 'directions')),
        (r'5tt|five_tt|segmentation|\.fast|\.bet|brain_mask|maskfilter|mask_image', ('segmentation', 'tissue')),
        (r'applywarp|applytransform|transform|warp(convert|invert|init|correct|2)|resample', ('registration', 'transforms')),
        (r'registration|register|flirt|fnirt|ants.registration', ('registration', 'alignment')),
        (r'denois|degibbs|noise', ('preprocessing', 'denoise')),
        (r'eddy|topup|slicetimer|mcflirt|bias|preproc|fmriprep|qsiprep|cbf|suvr', ('preprocessing', 'correction')),
        (r'freesurfer|surface|mesh|morphometr|cortical', ('structural', 'surface')),
        (r'mne|eeg|meg|electro|seed-', ('electrophysiology', 'signals')),
        (r'nilearn|fmri|bold|timeseries|functional', ('functional', 'timeseries')),
        (r'statistical|harmoniz|survival|causal|subtyp|genetic|brain-age|cnn|model|cpm|regression|learning|clusterstats|cfestats|vectorstats', ('statistics', 'modelling')),
        (r'nii2dcm|dcm2niix|convert|conversion', ('data', 'conversion')),
        (r'report|visual|qc|info|stats|check|inspect|header', ('quality', 'reports')),
        (r'bids|dataset|organ|manifest|inventory', ('data', 'organization')),
        (r'table|csv|json|log', ('utilities', 'tables')),
    ]
    for pattern, category in rules:
        if re.search(pattern, text):
            return category
    return ('utilities', 'images')


def software_for(method, group=''):
    command = method.execution.get('contract', {})
    family = command.get('software')
    if family:
        return family
    identity = method.id.lower()
    for key in ('mrtrix', 'freesurfer', 'fsl', 'ants', 'dipy', 'nilearn', 'mne', 'nibabel', 'dcm2niix'):
        if key in identity:
            return 'mrtrix3' if key == 'mrtrix' else key
    source = method.execution.get('adapter', method.execution.get('spec', method.execution))
    modules = source.get('modules', [])
    return modules[0].split('.')[0] if modules else group or identity.split('.')[0]


def category_labels(category, language='zh'):
    column = 1 if language == 'en' else 0
    return [(GROUPS | SUBGROUPS).get(key, (key, key))[column] for key in category]


def catalog_entries(definitions, user_pipelines=()):
    definitions.load()
    method_paths = {p.parent.name: p.parent.parent.name for p in definitions.root.glob('methods/*/*/method.json')}
    method_entries = {}
    for method in definitions.methods.values():
        category = method_category(method)
        method_entries[method.id] = dict(id=method.id, kind='method', label=method.label,
            description=method.description, version=method.version, category_ids=list(category),
            category=category_labels(category), software=[software_for(method, method_paths.get(method.id, ''))],
            input_types=method.input_types, output_types=method.output_types,
            omitted_option_count=len(method.execution.get('contract',{}).get('omitted_options',{})),parameter_count=len(method.parameters))
    latest = {p['id']: p for p in user_pipelines}
    pipeline_entries = {}
    for pipeline in [*definitions.pipelines.values(), *latest.values()]:
        members = [method_entries[s.get('method_ref', s.get('method_id'))] for s in pipeline['steps']
                   if s.get('method_ref', s.get('method_id')) in method_entries]
        # The final useful output is a better navigation anchor than the first
        # conversion step. Additional categories remain searchable facets.
        category = members[-1]['category_ids'] if members else ['utilities', 'guidance']
        pipeline_entries[pipeline['id']] = dict(id=pipeline['id'], kind='pipeline', label=pipeline['label'],
            description=pipeline.get('description', ''), version=pipeline['version'],status=pipeline.get('status','published'),deletion_revision=pipeline.get('deletion_revision',0),
            category_ids=category, category=category_labels(category),
            categories=[list(c) for c in dict.fromkeys(tuple(m['category_ids']) for m in members)] or [category],
            software=sorted({s for m in members for s in m['software']}), step_count=len(pipeline['steps']),
            method_ids=[m['id'] for m in members], user_defined=pipeline['id'] in latest)
    skill_entries = []
    for skill in definitions.skills.values():
        members = [method_entries[r['method_ref']] for r in skill['method_refs']]
        members += [pipeline_entries[r['pipeline_ref']] for r in skill['pipeline_refs']]
        categories = [list(c) for c, _ in Counter(tuple(m['category_ids']) for m in members).most_common()]
        if skill.get('scope') == 'dataset':
            categories = [['datasets', 'cohorts']]
        if not categories:
            sid = skill['id']
            top = ('electrophysiology' if any(t in sid for t in ('eeg','meg','seed')) else
                   'functional' if any(t in sid for t in ('fmri','bold')) else
                   'statistics' if any(t in sid for t in ('model','learning','predict')) else 'data')
            categories = [[top, 'guidance']]
        skill_entries.append(dict(id=skill['id'], kind='skill', label=skill.get('name', skill['id']),
            description=skill.get('description', ''), category_ids=categories[0], categories=categories,
            category=category_labels(categories[0]), software=sorted({s for m in members for s in m['software']}),
            method_count=len(skill['method_refs']), pipeline_count=len(skill['pipeline_refs']),
            method_ids=[r['method_ref'] for r in skill['method_refs']], pipeline_ids=[r['pipeline_ref'] for r in skill['pipeline_refs']]))
    return [*skill_entries, *method_entries.values(), *pipeline_entries.values()]


def facets(items, language='zh'):
    counts = Counter()
    software = Counter()
    for item in items:
        # One item may support several functions, but count it only once in each
        # parent category. Counts cover the full result set before pagination.
        paths = item.get('categories', [item['category_ids']])
        for prefix in {tuple(path[:depth]) for path in paths for depth in range(1, len(path)+1)}:
            counts[prefix] += 1
        software.update(set(item['software']))
    return {
        'categories': [{'path': list(path), 'label': category_labels(path, language)[-1], 'count': count}
                       for path, count in sorted(counts.items())],
        'software': [{'id': name, 'label': name, 'count': count} for name, count in sorted(software.items())],
    }
