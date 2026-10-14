"""Directional container compatibility and conservative, explicit semantic preservation.

Compatibility admits a file to a port; it never certifies spatial alignment or QC.
Specialized output types may flow into generic readers, never the reverse.
"""
IMAGE_TYPES=frozenset({'imaging_input','t1w_image','bold_image','preprocessed_dwi','raw_dwi',
    'brain_mask','five_tt_image','seed_mask','scalar_image','label_image','parcellation_image',
    'registered_image','fod_image','z_stat_map','contrast_image'})
FILE_TYPES=IMAGE_TYPES|{'transform_matrix','gradient_table','b_values','b_vectors','response_functions',
    'tractogram','streamline_weights','connectome','connectome_matrix','roi_timeseries','generated_file'}

def accepts_format(path, formats):
    """Empty declarations are unconstrained; declared suffixes are authoritative."""
    return not formats or any(str(path).lower().endswith(f.lower()) for f in formats)


def canonical_input(artifact_type, path, slot_id=''):
    """A consumer's spelling is not the identity of a gradient file."""
    roles = {'b_values': {'bval', 'bvals', 'fslgrad_bvals'},
             'b_vectors': {'bvec', 'bvecs', 'fslgrad_bvecs'}}
    if artifact_type == 'gradient_table':
        for kind, suffix in (('b_values', '.bval'), ('b_vectors', '.bvec')):
            if str(path).lower().endswith(suffix):
                artifact_type = kind
                break
        if artifact_type == 'gradient_table':
            artifact_type = next((kind for kind, names in roles.items() if slot_id in names), artifact_type)
    if artifact_type in roles and any(slot_id in names for kind,names in roles.items() if kind != artifact_type):
        raise ValueError('输入语义与梯度端口冲突；修正 bval/bvec 映射，不需要重新查询目录')
    if slot_id in roles.get(artifact_type, set()):
        slot_id = ''
    return artifact_type, slot_id

def compatible(source, target):
    return bool(source and target) and (source==target or target=='imaging_input' and source in IMAGE_TYPES
        or target=='gradient_table' and source in {'b_values','b_vectors'}
        or target=='generated_file' and source in FILE_TYPES
        or target=='analysis_input' and source in FILE_TYPES)

def specialize(method, inputs, parameters):
    result=method.model_copy(deep=True)
    identity=getattr(method,'definition_id','') or method.id
    if identity!='mrtrix.command.mrtransform':
        return result
    for slot in result.output_slots:
        if slot.id=='output':
            slot.type='imaging_input'
    result.output_types=list(dict.fromkeys(s.type for s in result.output_slots))
    kind=inputs.get('input')
    # Label interpolation must preserve integer identities; intensity modulation
    # destroys that meaning. Unknown operations deliberately keep generic output.
    preserve=kind in {'parcellation_image','label_image','brain_mask','seed_mask'} and parameters.get('interp')=='nearest' and not parameters.get('modulate')
    if preserve:
        for slot in result.output_slots:
            if slot.id=='output':
                slot.type=kind
        result.output_types=list(dict.fromkeys(s.type for s in result.output_slots))
    return result
