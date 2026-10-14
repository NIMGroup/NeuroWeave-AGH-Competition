"""Declared gradient alternatives shared by preflight and command assembly."""


def roles(context):
    slots = {s.id: s for s in context.method.input_slots}
    image = next(
        (
            k
            for k, s in slots.items()
            if k in {"dwi", "input"}
            and s.type
            in {"raw_dwi", "preprocessed_dwi", "imaging_input", "analysis_input"}
        ),
        None,
    )
    return (
        image,
        next((k for k in ("bvecs", "fslgrad_bvecs") if k in slots), None),
        next((k for k in ("bvals", "fslgrad_bvals") if k in slots), None),
        "grad" if "grad" in slots else None,
    )


def gradient_slots(context, except_source):
    _, bv, ba, g = roles(context)
    return {
        v
        for v in (bv, ba, g)
        if v
        and not (
            except_source == "fsl"
            and v in {bv, ba}
            or except_source == "mrtrix"
            and v == g
        )
    }


def gradient_arguments(context):
    from .scientific_checks import image_header, gradient_table
    from .external_command import ExternalCommandRunner as Runner

    image, bvec, bval, grad = roles(context)
    source = context.step.selected_parameters.get("gradient_source", "auto")
    if not image:
        raise ValueError("梯度契约缺少明确的DWI输入角色")
    if context.input_is_deferred(image):
        return [], "deferred", {}
    path = Runner.required_binding(context, image)
    header = image_header(path)
    if header["status"] != "passed":
        raise ValueError("无法读取DWI头以选择梯度来源：" + header["code"])
    if len(header.get("shape", [])) < 4:
        raise ValueError("DWI必须包含第四维体积序列")
    embedded = header.get("embedded_gradients")
    if source == "auto":
        source = (
            "embedded"
            if embedded
            else "fsl"
            if bvec in context.step.input_bindings
            and bval in context.step.input_bindings
            else ("mrtrix" if grad in context.step.input_bindings else "")
        )
    if source == "embedded":
        if not embedded or not embedded.get("gradients_consistent"):
            raise ValueError("图像没有与体积数一致的内嵌梯度；请提供配套外部梯度")
        profile = embedded
        args = []
    elif source == "fsl":
        if context.input_is_deferred(bvec) and context.input_is_deferred(bval):
            return ["-fslgrad", "<upstream:bvec>", "<upstream:bval>"], "deferred", {}
        vec = Runner.required_binding(context, bvec)
        val = Runner.required_binding(context, bval)
        profile = gradient_table(val, "b_values")
        if gradient_table(vec, "b_vectors")["count"] != profile["count"]:
            raise ValueError("bval与bvec数量不一致")
        args = ["-fslgrad", str(vec), str(val)]
    elif source == "mrtrix":
        table = Runner.required_binding(context, grad)
        profile = gradient_table(table, "gradient_table")
        args = ["-grad", str(table)]
    else:
        raise ValueError("缺少内嵌梯度或配套外部梯度；在输入绑定中指定来源")
    if profile.get("volume_count", profile.get("count")) != header["shape"][3]:
        raise ValueError("选用梯度的数量与DWI体积数不一致")
    return args, source, profile
