//! Reflection of compiled SPIR-V. This is an ABI gate, not a semantic/safety validator.
use rspirv::dr::{Instruction, Operand};
use rspirv::spirv::{
    Decoration, Dim, ExecutionMode, ExecutionModel, ImageFormat, Op, StorageClass,
};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Abi {
    pub version: u32,
    pub entry_point: String,
    pub local_size: [u32; 3],
    pub input: String,
    pub output: String,
}

fn id(o: Option<&Operand>) -> Result<u32, String> {
    match o {
        Some(Operand::IdRef(v)) => Ok(*v),
        _ => Err("expected SPIR-V ID".into()),
    }
}
fn lit(o: Option<&Operand>) -> Result<u32, String> {
    match o {
        Some(Operand::LiteralBit32(v)) => Ok(*v),
        _ => Err("expected literal integer".into()),
    }
}

pub fn reflect(bytes: &[u8]) -> Result<Abi, String> {
    if bytes.len() < 20 || !bytes.len().is_multiple_of(4) || bytes[..4] != [3, 2, 35, 7] {
        return Err("SPIR-V must be a little-endian word stream".into());
    }
    let m = rspirv::dr::load_bytes(bytes).map_err(|e| format!("invalid SPIR-V: {e}"))?;
    if m.memory_model.as_ref().map(|i| &i.operands)
        != Some(&vec![
            Operand::AddressingModel(rspirv::spirv::AddressingModel::Logical),
            Operand::MemoryModel(rspirv::spirv::MemoryModel::GLSL450),
        ])
    {
        return Err("only Logical GLSL450 memory model supported".into());
    }
    if m.entry_points.len() != 1 {
        return Err("exactly one compute entry point required".into());
    }
    let ep = &m.entry_points[0].operands;
    if ep.first() != Some(&Operand::ExecutionModel(ExecutionModel::GLCompute))
        || ep.get(2) != Some(&Operand::LiteralString("main".into()))
    {
        return Err("entry point must be compute main".into());
    }
    let entry = id(ep.get(1))?;
    if !m
        .functions
        .iter()
        .any(|f| f.def.as_ref().and_then(|i| i.result_id) == Some(entry))
    {
        return Err("entry point function missing".into());
    }
    if m.execution_modes.len() != 1 {
        return Err("exactly one fixed LocalSize execution mode required".into());
    }
    let mode = &m.execution_modes[0];
    if mode.class.opcode != Op::ExecutionMode
        || id(mode.operands.first())? != entry
        || mode.operands.get(1) != Some(&Operand::ExecutionMode(ExecutionMode::LocalSize))
        || mode.operands.len() != 5
        || [
            lit(mode.operands.get(2))?,
            lit(mode.operands.get(3))?,
            lit(mode.operands.get(4))?,
        ] != [16, 16, 1]
    {
        return Err("local size must be fixed 16x16x1".into());
    }
    let mut defs = BTreeMap::<u32, &Instruction>::new();
    for i in m.all_inst_iter() {
        if let Some(v) = i.result_id {
            if defs.insert(v, i).is_some() {
                return Err("duplicate SPIR-V result ID".into());
            }
        }
        if matches!(
            i.class.opcode,
            Op::SpecConstant
                | Op::SpecConstantTrue
                | Op::SpecConstantFalse
                | Op::SpecConstantComposite
                | Op::SpecConstantOp
        ) {
            return Err("specialization constants unsupported by this artifact ABI".into());
        }
    }
    let mut decorations = BTreeMap::<u32, BTreeMap<Decoration, Vec<Operand>>>::new();
    for i in &m.annotations {
        if i.class.opcode != Op::Decorate {
            return Err("decoration groups/member decorations unsupported".into());
        }
        let target = id(i.operands.first())?;
        if !defs.contains_key(&target) {
            return Err("decoration targets missing ID".into());
        }
        let Some(Operand::Decoration(d)) = i.operands.get(1) else {
            return Err("invalid decoration".into());
        };
        if decorations
            .entry(target)
            .or_default()
            .insert(*d, i.operands[2..].to_vec())
            .is_some()
        {
            return Err("duplicate decoration".into());
        }
    }
    let mut seen = BTreeSet::new();
    for var in m
        .types_global_values
        .iter()
        .filter(|i| i.class.opcode == Op::Variable)
    {
        let var_id = var.result_id.ok_or("variable ID missing")?;
        let Some(Operand::StorageClass(storage)) = var.operands.first() else {
            return Err("variable storage missing".into());
        };
        let deco = decorations.get(&var_id);
        let has_binding = deco.is_some_and(|d| {
            d.contains_key(&Decoration::Binding) || d.contains_key(&Decoration::DescriptorSet)
        });
        if *storage != StorageClass::UniformConstant {
            if has_binding
                || !matches!(
                    storage,
                    StorageClass::Input | StorageClass::Private | StorageClass::Workgroup
                )
            {
                return Err("extra buffer, push constant or unsupported global resource".into());
            }
            if *storage == StorageClass::Input
                && !deco.is_some_and(|d| d.contains_key(&Decoration::BuiltIn))
            {
                return Err("compute input must be builtin".into());
            }
            continue;
        }
        let d = deco.ok_or("resource has no binding decorations")?;
        let set = d
            .get(&Decoration::DescriptorSet)
            .ok_or("descriptor set missing")?;
        let binding = d
            .get(&Decoration::Binding)
            .ok_or("descriptor binding missing")?;
        if set.len() != 1 || binding.len() != 1 || lit(set.first())? != 0 {
            return Err("set must be 0".into());
        }
        let b = lit(binding.first())?;
        if b > 1 || !seen.insert(b) {
            return Err("only unique bindings 0 and 1 allowed".into());
        }
        let ptr = defs
            .get(&var.result_type.ok_or("variable pointer type missing")?)
            .ok_or("undefined pointer")?;
        if ptr.class.opcode != Op::TypePointer
            || ptr.operands.first() != Some(&Operand::StorageClass(StorageClass::UniformConstant))
        {
            return Err("resource must be UniformConstant pointer".into());
        }
        let ty = defs
            .get(&id(ptr.operands.get(1))?)
            .ok_or("undefined resource type")?;
        let img = if b == 0 {
            if ty.class.opcode != Op::TypeSampledImage {
                return Err("input must be scalar combined sampler".into());
            }
            defs.get(&id(ty.operands.first())?)
                .ok_or("undefined sampled image type")?
        } else {
            if ty.class.opcode != Op::TypeImage
                || !d.contains_key(&Decoration::NonReadable)
                || d.contains_key(&Decoration::NonWritable)
            {
                return Err("output must be scalar write-only storage image".into());
            }
            ty
        };
        let a = &img.operands;
        if img.class.opcode != Op::TypeImage
            || a.len() != 7
            || a.get(1) != Some(&Operand::Dim(Dim::Dim2D))
            || lit(a.get(2))? != 0
            || lit(a.get(3))? != 0
            || lit(a.get(4))? != 0
            || lit(a.get(5))? != if b == 0 { 1 } else { 2 }
            || a.get(6)
                != Some(&Operand::ImageFormat(if b == 0 {
                    ImageFormat::Unknown
                } else {
                    ImageFormat::Rgba8
                }))
        {
            return Err(
                "image must be non-array, non-MS 2D sampled input / rgba8 storage output".into(),
            );
        }
        let float = defs
            .get(&id(a.first())?)
            .ok_or("undefined image component type")?;
        if float.class.opcode != Op::TypeFloat || lit(float.operands.first())? != 32 {
            return Err("image component type must be float32".into());
        }
    }
    if seen != BTreeSet::from([0, 1]) {
        return Err("both sampled input and storage output required".into());
    }
    Ok(Abi {
        version: 1,
        entry_point: "main".into(),
        local_size: [16, 16, 1],
        input: "set0/binding0:combined_sampler_2d_float32".into(),
        output: "set0/binding1:writeonly_rgba8_2d".into(),
    })
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use rspirv::{binary::Assemble, dr::Builder, spirv};

    pub fn fixture(
        binding: u32,
        local: u32,
        output_readable: bool,
        storage_input: bool,
        extra: bool,
    ) -> Vec<u8> {
        let mut b = Builder::new();
        b.set_version(1, 0);
        b.capability(spirv::Capability::Shader);
        b.memory_model(spirv::AddressingModel::Logical, spirv::MemoryModel::GLSL450);
        let f = b.type_float(32, None);
        let input_img = b.type_image(
            f,
            Dim::Dim2D,
            0,
            0,
            0,
            if storage_input { 2 } else { 1 },
            ImageFormat::Unknown,
            None,
        );
        let sampled = if storage_input {
            input_img
        } else {
            b.type_sampled_image(input_img)
        };
        let ip = b.type_pointer(None, StorageClass::UniformConstant, sampled);
        let iv = b.variable(ip, None, StorageClass::UniformConstant, None);
        b.decorate(iv, Decoration::DescriptorSet, [Operand::LiteralBit32(0)]);
        b.decorate(iv, Decoration::Binding, [Operand::LiteralBit32(binding)]);
        let out = b.type_image(f, Dim::Dim2D, 0, 0, 0, 2, ImageFormat::Rgba8, None);
        let op = b.type_pointer(None, StorageClass::UniformConstant, out);
        let ov = b.variable(op, None, StorageClass::UniformConstant, None);
        b.decorate(ov, Decoration::DescriptorSet, [Operand::LiteralBit32(0)]);
        b.decorate(ov, Decoration::Binding, [Operand::LiteralBit32(1)]);
        if !output_readable {
            b.decorate(ov, Decoration::NonReadable, []);
        }
        if extra {
            let ev = b.variable(op, None, StorageClass::UniformConstant, None);
            b.decorate(ev, Decoration::DescriptorSet, [Operand::LiteralBit32(0)]);
            b.decorate(ev, Decoration::Binding, [Operand::LiteralBit32(2)]);
        }
        let void = b.type_void();
        let ft = b.type_function(void, []);
        let fun = b
            .begin_function(void, None, spirv::FunctionControl::NONE, ft)
            .unwrap();
        b.begin_block(None).unwrap();
        b.ret().unwrap();
        b.end_function().unwrap();
        b.entry_point(ExecutionModel::GLCompute, fun, "main", []);
        b.execution_mode(fun, ExecutionMode::LocalSize, [local, 16, 1]);
        b.module()
            .assemble()
            .into_iter()
            .flat_map(u32::to_le_bytes)
            .collect()
    }
    #[test]
    fn reflects_instruction_types_and_rejects_wrong_abi() {
        assert!(reflect(&fixture(0, 16, false, false, false)).is_ok());
        for (binding, local, readable, storage, extra) in [
            (2, 16, false, false, false),
            (0, 8, false, false, false),
            (0, 16, true, false, false),
            (0, 16, false, true, false),
            (0, 16, false, false, true),
        ] {
            assert!(reflect(&fixture(binding, local, readable, storage, extra)).is_err());
        }
        assert!(reflect(&[3, 2, 35]).is_err());
    }
    #[test]
    fn rejects_format_shape_set_access_push_constants_and_entry_mutations() {
        let original = rspirv::dr::load_bytes(fixture(0, 16, false, false, false)).unwrap();
        for case in [
            "format",
            "dimension",
            "array",
            "ms",
            "float64",
            "set",
            "nonwritable",
            "push_constant",
            "entry_name",
            "entry_model",
            "extra_entry",
            "missing_function",
            "spec_local",
        ] {
            let mut m = original.clone();
            match case {
                "format" | "dimension" | "array" | "ms" => {
                    let image = m
                        .types_global_values
                        .iter_mut()
                        .find(|i| {
                            i.class.opcode == Op::TypeImage
                                && i.operands[5] == Operand::LiteralBit32(2)
                        })
                        .unwrap();
                    let (index, value) = match case {
                        "format" => (6, Operand::ImageFormat(ImageFormat::Rgba32f)),
                        "dimension" => (1, Operand::Dim(Dim::Dim3D)),
                        "array" => (3, Operand::LiteralBit32(1)),
                        _ => (4, Operand::LiteralBit32(1)),
                    };
                    image.operands[index] = value;
                }
                "float64" => {
                    m.types_global_values
                        .iter_mut()
                        .find(|i| i.class.opcode == Op::TypeFloat)
                        .unwrap()
                        .operands[0] = Operand::LiteralBit32(64)
                }
                "set" => {
                    m.annotations
                        .iter_mut()
                        .find(|i| {
                            i.operands.get(1)
                                == Some(&Operand::Decoration(Decoration::DescriptorSet))
                        })
                        .unwrap()
                        .operands[2] = Operand::LiteralBit32(1)
                }
                "nonwritable" => {
                    m.annotations
                        .iter_mut()
                        .find(|i| {
                            i.operands.get(1) == Some(&Operand::Decoration(Decoration::NonReadable))
                        })
                        .unwrap()
                        .operands[1] = Operand::Decoration(Decoration::NonWritable)
                }
                "push_constant" => {
                    m.types_global_values
                        .iter_mut()
                        .find(|i| i.class.opcode == Op::Variable)
                        .unwrap()
                        .operands[0] = Operand::StorageClass(StorageClass::PushConstant)
                }
                "entry_name" => {
                    m.entry_points[0].operands[2] = Operand::LiteralString("other".into())
                }
                "entry_model" => {
                    m.entry_points[0].operands[0] = Operand::ExecutionModel(ExecutionModel::Vertex)
                }
                "extra_entry" => m.entry_points.push(m.entry_points[0].clone()),
                "missing_function" => m.functions.clear(),
                "spec_local" => {
                    let mode = &mut m.execution_modes[0];
                    *mode = Instruction::new(
                        Op::ExecutionModeId,
                        None,
                        None,
                        vec![
                            mode.operands[0].clone(),
                            Operand::ExecutionMode(ExecutionMode::LocalSizeId),
                            Operand::IdRef(1),
                            Operand::IdRef(1),
                            Operand::IdRef(1),
                        ],
                    );
                }
                _ => unreachable!(),
            }
            let bytes: Vec<_> = m
                .assemble()
                .into_iter()
                .flat_map(u32::to_le_bytes)
                .collect();
            assert!(reflect(&bytes).is_err(), "{case}");
        }
    }
}
