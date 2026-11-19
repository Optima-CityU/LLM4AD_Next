//! Finite SPIR-V resource adaptation. No compiler/device/model/safety qualification.
use crate::upopabi;
use rspirv::{
    binary::Assemble,
    dr::{Instruction, Module, Operand},
    spirv::{Decoration, Dim, ImageFormat, ImageOperands, Op, StorageClass},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::Read,
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};

const VERSION: u32 = 1;
const SOURCE_LIMIT: u64 = 4 * 1024 * 1024;
const SPV_LIMIT: u64 = 16 * 1024 * 1024;
const RECORD_LIMIT: u64 = 64 * 1024;
const INSTRUCTION_LIMIT: usize = 131_072;
const ASSOCIATION: &str = "caller_supplied_unverified";
const FILES: [&str; 4] = [
    "original.comp",
    "original.spv",
    "adapted.spv",
    "descriptor.json",
];
static SEQ: AtomicU64 = AtomicU64::new(0);

fn id(o: Option<&Operand>) -> Result<u32, String> {
    match o {
        Some(Operand::IdRef(v)) => Ok(*v),
        _ => Err("expected typed ID operand".into()),
    }
}
fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn bytes(m: &Module) -> Vec<u8> {
    m.assemble().iter().flat_map(|w| w.to_le_bytes()).collect()
}
fn parse(data: &[u8]) -> Result<Module, String> {
    if data.len() < 20 || !data.len().is_multiple_of(4) || data[..4] != [3, 2, 35, 7] {
        return Err("SPIR-V must be little-endian words".into());
    }
    // Bound parser allocation independently of byte size (e.g. millions of OpNop).
    let mut offset = 20usize;
    let mut count = 0usize;
    while offset < data.len() {
        let size =
            (u32::from_le_bytes(data[offset..offset + 4].try_into().unwrap()) >> 16) as usize * 4;
        if size == 0 || size > data.len() - offset {
            return Err("invalid instruction word count".into());
        }
        offset += size;
        count += 1;
        if count > INSTRUCTION_LIMIT {
            return Err("SPIR-V instruction count limit exceeded".into());
        }
    }
    let m = rspirv::dr::load_bytes(data).map_err(|e| format!("invalid SPIR-V: {e}"))?;
    let mut definitions = BTreeSet::new();
    let bound = m.header.as_ref().ok_or("SPIR-V header missing")?.bound;
    for i in m.all_inst_iter() {
        if let Some(v) = i.result_id {
            if v == 0 || v >= bound || !definitions.insert(v) {
                return Err("duplicate, zero or out-of-bound result ID".into());
            }
        }
    }
    let mut prior_globals = BTreeSet::new();
    for i in &m.types_global_values {
        if i.result_type.is_some_and(|v| !prior_globals.contains(&v))
            || i.operands
                .iter()
                .any(|o| matches!(o, Operand::IdRef(v) if !prior_globals.contains(v)))
        {
            return Err(
                "global type/use declaration order unsupported (including forward pointer types)"
                    .into(),
            );
        }
        if let Some(v) = i.result_id {
            prior_globals.insert(v);
        }
    }
    for i in m.all_inst_iter() {
        if i.result_type.is_some_and(|v| !definitions.contains(&v))
            || i.operands
                .iter()
                .any(|o| matches!(o, Operand::IdRef(v) if !definitions.contains(v)))
        {
            return Err("undefined typed ID reference".into());
        }
    }
    Ok(m)
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Changes {
    pub input_variable: u32,
    pub output_variable: u32,
    pub original_image_type: u32,
    pub original_pointer_type: u32,
    pub output_shared_original_type: bool,
    pub direct_loads: u32,
    pub unused_loads: u32,
    pub image_reads_to_fetch: u32,
    pub image_sizes_to_lod: u32,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Descriptor {
    protocol_version: u32,
    original_source_sha256: String,
    original_spirv_sha256: String,
    adapted_spirv_sha256: String,
    source_spirv_association: String,
    transform: String,
    changes: Changes,
    adapted_abi: upopabi::Abi,
}

fn vector_type(defs: &BTreeMap<u32, &Instruction>, ty: u32, float: bool, count: u32) -> bool {
    let Some(v) = defs.get(&ty) else { return false };
    if v.class.opcode != Op::TypeVector
        || v.operands.len() != 2
        || v.operands.get(1) != Some(&Operand::LiteralBit32(count))
    {
        return false;
    }
    let Ok(scalar) = id(v.operands.first()) else {
        return false;
    };
    let Some(s) = defs.get(&scalar) else {
        return false;
    };
    if float {
        s.class.opcode == Op::TypeFloat && s.operands == [Operand::LiteralBit32(32)]
    } else {
        s.class.opcode == Op::TypeInt
            && s.operands.len() == 2
            && s.operands[0] == Operand::LiteralBit32(32)
            && matches!(s.operands[1], Operand::LiteralBit32(0 | 1))
    }
}

fn fresh(next: &mut u32) -> Result<u32, String> {
    let v = *next;
    *next = next.checked_add(1).ok_or("SPIR-V ID space exhausted")?;
    Ok(v)
}
fn intern_type(
    m: &mut Module,
    next: &mut u32,
    input_variable: u32,
    op: Op,
    operands: Vec<Operand>,
) -> Result<u32, String> {
    if let Some(i) = m
        .types_global_values
        .iter()
        .find(|i| i.class.opcode == op && i.operands == operands)
    {
        return i.result_id.ok_or_else(|| "type missing ID".into());
    }
    let result = fresh(next)?;
    let before_global = m
        .types_global_values
        .iter()
        .position(|i| i.result_id == Some(input_variable))
        .ok_or("input must be a global variable")?;
    for operand in &operands {
        if let Operand::IdRef(dependency) = operand {
            if !m.types_global_values[..before_global]
                .iter()
                .any(|i| i.result_id == Some(*dependency))
            {
                return Err(
                    "new input type dependency must be declared before input variable".into(),
                );
            }
        }
    }
    m.types_global_values.insert(
        before_global,
        Instruction::new(op, None, Some(result), operands),
    );
    Ok(result)
}

/// Structural transform only; original safety gate and device equivalence are separate.
pub fn adapt(data: &[u8]) -> Result<(Vec<u8>, Changes, upopabi::Abi), String> {
    if data.len() as u64 > SPV_LIMIT {
        return Err("SPIR-V size limit exceeded".into());
    }
    let mut m = parse(data)?;
    let defs: BTreeMap<_, _> = m
        .all_inst_iter()
        .filter_map(|i| i.result_id.map(|v| (v, i)))
        .collect();
    let mut resources = BTreeMap::new();
    let mut decoration_keys = BTreeSet::new();
    for i in &m.annotations {
        if i.class.opcode == Op::Decorate {
            let Some(Operand::Decoration(d)) = i.operands.get(1) else {
                return Err("decoration missing".into());
            };
            if !decoration_keys.insert((id(i.operands.first())?, *d)) {
                return Err("duplicate original decoration".into());
            }
        }
        if i.class.opcode == Op::Decorate
            && i.operands.get(1) == Some(&Operand::Decoration(Decoration::Binding))
        {
            let var = id(i.operands.first())?;
            let Some(Operand::LiteralBit32(b)) = i.operands.get(2) else {
                return Err("invalid binding".into());
            };
            if resources.insert(*b, var).is_some() {
                return Err("duplicate binding".into());
            }
        }
    }
    if resources.keys().copied().collect::<Vec<_>>() != [0, 1] {
        return Err("only bindings 0 and 1 required".into());
    }
    let input = resources[&0];
    let output = resources[&1];
    let var = defs.get(&input).ok_or("input variable missing")?;
    if !m
        .types_global_values
        .iter()
        .any(|i| i.result_id == Some(input))
        || var.class.opcode != Op::Variable
        || var.operands != [Operand::StorageClass(StorageClass::UniformConstant)]
    {
        return Err(
            "input must be direct UniformConstant global variable without initializer".into(),
        );
    }
    let ptr_id = var.result_type.ok_or("input pointer type missing")?;
    let ptr = defs.get(&ptr_id).ok_or("input pointer undefined")?;
    if ptr.class.opcode != Op::TypePointer
        || ptr.operands.len() != 2
        || ptr.operands[0] != Operand::StorageClass(StorageClass::UniformConstant)
    {
        return Err("input must have UniformConstant pointer type".into());
    }
    let img_id = id(ptr.operands.get(1))?;
    let img = defs.get(&img_id).ok_or("input image undefined")?;
    if img.class.opcode != Op::TypeImage
        || img.operands.len() != 7
        || img.operands[1..]
            != [
                Operand::Dim(Dim::Dim2D),
                Operand::LiteralBit32(0),
                Operand::LiteralBit32(0),
                Operand::LiteralBit32(0),
                Operand::LiteralBit32(2),
                Operand::ImageFormat(ImageFormat::Rgba8),
            ]
    {
        return Err("input must be scalar non-MS non-depth rgba8 storage image2D".into());
    }
    let scalar = id(img.operands.first())?;
    if !defs.get(&scalar).is_some_and(|i| {
        i.class.opcode == Op::TypeFloat && i.operands == [Operand::LiteralBit32(32)]
    }) {
        return Err("input image component must be float32".into());
    }
    let input_decorations: Vec<_> = m
        .annotations
        .iter()
        .filter(|i| i.operands.first() == Some(&Operand::IdRef(input)))
        .collect();
    if !input_decorations.iter().any(|i| {
        i.class.opcode == Op::Decorate
            && i.operands
                == [
                    Operand::IdRef(input),
                    Operand::Decoration(Decoration::NonWritable),
                ]
    }) {
        return Err("input must be readonly (NonWritable)".into());
    }
    for i in &input_decorations {
        if i.class.opcode != Op::Decorate
            || !matches!(
                i.operands.get(1),
                Some(Operand::Decoration(
                    Decoration::Binding | Decoration::DescriptorSet | Decoration::NonWritable
                ))
            )
        {
            return Err("unsupported input decoration".into());
        }
    }

    let mut loads = BTreeSet::new();
    for i in m
        .all_inst_iter()
        .filter(|i| i.operands.contains(&Operand::IdRef(input)))
    {
        let allowed = match i.class.opcode {
            Op::Name => i.operands.len() == 2 && id(i.operands.first())? == input,
            Op::Decorate => id(i.operands.first())? == input,
            Op::EntryPoint => i
                .operands
                .iter()
                .skip(3)
                .any(|o| *o == Operand::IdRef(input)),
            Op::Load => {
                if i.operands != [Operand::IdRef(input)] || i.result_type != Some(img_id) {
                    return Err("input load must be direct and have the original image type".into());
                }
                loads.insert(i.result_id.ok_or("input load missing result ID")?);
                true
            }
            _ => false,
        };
        if !allowed {
            return Err(format!(
                "unsupported input pointer use {:?}",
                i.class.opcode
            ));
        }
    }
    if loads.is_empty() {
        return Err("input has no direct load".into());
    }
    let block_loads: BTreeSet<_> = m
        .functions
        .iter()
        .flat_map(|f| &f.blocks)
        .flat_map(|b| &b.instructions)
        .filter(|i| i.class.opcode == Op::Load && i.operands == [Operand::IdRef(input)])
        .filter_map(|i| i.result_id)
        .collect();
    if block_loads != loads {
        return Err("input load must occur in a function block".into());
    }
    let mut reads = 0;
    let mut sizes = 0;
    let mut unused = 0;
    let mut consumers_by_load = BTreeMap::<u32, Vec<&Instruction>>::new();
    for i in m.all_inst_iter() {
        let referenced: BTreeSet<_> = i
            .operands
            .iter()
            .filter_map(|o| match o {
                Operand::IdRef(v) if loads.contains(v) => Some(*v),
                _ => None,
            })
            .collect();
        for load in referenced {
            consumers_by_load.entry(load).or_default().push(i);
        }
    }
    for load in &loads {
        let mut consumers = 0;
        for i in consumers_by_load.get(load).into_iter().flatten() {
            if i.class.opcode == Op::Name
                && i.operands.len() == 2
                && i.operands.first() == Some(&Operand::IdRef(*load))
            {
                continue;
            }
            if i.operands.first() != Some(&Operand::IdRef(*load))
                || i.operands
                    .iter()
                    .filter(|o| **o == Operand::IdRef(*load))
                    .count()
                    != 1
            {
                return Err("input resource used in unsupported operand position".into());
            }
            match i.class.opcode {
                Op::ImageRead => {
                    if i.operands.len() != 2 {
                        return Err("input ImageRead optional operands unsupported".into());
                    }
                    let coord = id(i.operands.get(1))?;
                    let coord_ty = defs
                        .get(&coord)
                        .and_then(|i| i.result_type)
                        .ok_or("coordinate type missing")?;
                    if !vector_type(&defs, coord_ty, false, 2)
                        || !vector_type(
                            &defs,
                            i.result_type.ok_or("read result type missing")?,
                            true,
                            4,
                        )
                    {
                        return Err(
                            "ImageRead requires int32x2 coordinate and float32x4 result".into()
                        );
                    }
                    reads += 1;
                }
                Op::ImageQuerySize => {
                    if i.operands.len() != 1
                        || !vector_type(
                            &defs,
                            i.result_type.ok_or("size result type missing")?,
                            false,
                            2,
                        )
                    {
                        return Err(
                            "ImageQuerySize requires int32x2 result and no optional operands"
                                .into(),
                        );
                    }
                    sizes += 1;
                }
                _ => {
                    return Err(format!(
                        "unsupported input image consumer {:?}",
                        i.class.opcode
                    ))
                }
            }
            consumers += 1;
        }
        if consumers == 0 {
            unused += 1;
        }
    }
    if reads == 0 {
        return Err("input has no supported image read".into());
    }
    let output_ptr = defs
        .get(&output)
        .and_then(|i| i.result_type)
        .and_then(|ty| defs.get(&ty));
    let shared = output_ptr.is_some_and(|p| p.operands.get(1) == Some(&Operand::IdRef(img_id)));
    let changes = Changes {
        input_variable: input,
        output_variable: output,
        original_image_type: img_id,
        original_pointer_type: ptr_id,
        output_shared_original_type: shared,
        direct_loads: loads.len() as u32,
        unused_loads: unused,
        image_reads_to_fetch: reads,
        image_sizes_to_lod: sizes,
    };
    let img_operands = img.operands.clone();
    // Original output types/pointers are never rewritten, even when shared.
    let mut next = m.header.as_ref().unwrap().bound;
    let mut new_image = img_operands;
    new_image[5] = Operand::LiteralBit32(1);
    new_image[6] = Operand::ImageFormat(ImageFormat::Unknown);
    let sampled_img = intern_type(&mut m, &mut next, input, Op::TypeImage, new_image)?;
    let combined = intern_type(
        &mut m,
        &mut next,
        input,
        Op::TypeSampledImage,
        vec![Operand::IdRef(sampled_img)],
    )?;
    let pointer = intern_type(
        &mut m,
        &mut next,
        input,
        Op::TypePointer,
        vec![
            Operand::StorageClass(StorageClass::UniformConstant),
            Operand::IdRef(combined),
        ],
    )?;
    let int_ty = intern_type(
        &mut m,
        &mut next,
        input,
        Op::TypeInt,
        vec![Operand::LiteralBit32(32), Operand::LiteralBit32(1)],
    )?;
    let zero = fresh(&mut next)?;
    m.types_global_values.push(Instruction::new(
        Op::Constant,
        Some(int_ty),
        Some(zero),
        vec![Operand::LiteralBit32(0)],
    ));
    m.types_global_values
        .iter_mut()
        .find(|i| i.result_id == Some(input))
        .unwrap()
        .result_type = Some(pointer);
    m.annotations.retain(|i| {
        i.operands
            != [
                Operand::IdRef(input),
                Operand::Decoration(Decoration::NonWritable),
            ]
    });
    for f in &mut m.functions {
        for block in &mut f.blocks {
            let mut rewritten = Vec::with_capacity(block.instructions.len());
            for mut i in std::mem::take(&mut block.instructions) {
                if i.class.opcode == Op::Load && i.operands == [Operand::IdRef(input)] {
                    let old_id = i.result_id.unwrap();
                    let loaded_combined = fresh(&mut next)?;
                    i.result_id = Some(loaded_combined);
                    i.result_type = Some(combined);
                    rewritten.push(i);
                    rewritten.push(Instruction::new(
                        Op::Image,
                        Some(sampled_img),
                        Some(old_id),
                        vec![Operand::IdRef(loaded_combined)],
                    ));
                    continue;
                }
                if i.operands
                    .first()
                    .is_some_and(|o| matches!(o, Operand::IdRef(v) if loads.contains(v)))
                {
                    if i.class.opcode == Op::ImageRead {
                        i = Instruction::new(
                            Op::ImageFetch,
                            i.result_type,
                            i.result_id,
                            vec![
                                i.operands[0].clone(),
                                i.operands[1].clone(),
                                Operand::ImageOperands(ImageOperands::LOD),
                                Operand::IdRef(zero),
                            ],
                        );
                    } else if i.class.opcode == Op::ImageQuerySize {
                        i = Instruction::new(
                            Op::ImageQuerySizeLod,
                            i.result_type,
                            i.result_id,
                            vec![i.operands[0].clone(), Operand::IdRef(zero)],
                        );
                    }
                }
                rewritten.push(i);
            }
            block.instructions = rewritten;
        }
    }
    m.header.as_mut().unwrap().bound = next;
    let adapted = bytes(&m);
    if adapted.len() as u64 > SPV_LIMIT {
        return Err("adapted SPIR-V size limit exceeded".into());
    }
    parse(&adapted)?;
    let abi = upopabi::reflect(&adapted)?;
    Ok((adapted, changes, abi))
}

fn read(p: &Path, limit: u64) -> Result<Vec<u8>, String> {
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW);
    }
    let f = options
        .open(p)
        .map_err(|e| format!("{}: {e}", p.display()))?;
    if !f.metadata().map_err(|e| e.to_string())?.is_file() {
        return Err("input must be a regular file".into());
    }
    let mut data = Vec::new();
    f.take(limit + 1)
        .read_to_end(&mut data)
        .map_err(|e| e.to_string())?;
    if data.len() as u64 > limit {
        return Err(format!("file exceeds {limit} byte limit"));
    }
    Ok(data)
}
fn pretty(v: &impl Serialize) -> Result<Vec<u8>, String> {
    serde_json::to_vec_pretty(v).map_err(|e| e.to_string())
}
fn descriptor(source: &[u8], original: &[u8]) -> Result<(Descriptor, Vec<u8>), String> {
    if source.is_empty() || std::str::from_utf8(source).is_err() {
        return Err("source snapshot must be nonempty UTF-8 text".into());
    }
    let (adapted, changes, abi) = adapt(original)?;
    Ok((
        Descriptor {
            protocol_version: VERSION,
            original_source_sha256: hash(source),
            original_spirv_sha256: hash(original),
            adapted_spirv_sha256: hash(&adapted),
            source_spirv_association: ASSOCIATION.into(),
            transform: "direct_rgba8_storage_to_combined_fetch_lod0".into(),
            changes,
            adapted_abi: abi,
        },
        adapted,
    ))
}
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    schema_version: u32,
    adaptation_id: String,
    state: String,
    deployable: bool,
    device_validation: String,
    semantic_equivalence: String,
    qualification: String,
    source_spirv_association: String,
    files: BTreeMap<String, String>,
}
pub fn snapshots(dir: &Path) -> Result<(BTreeMap<String, Vec<u8>>, Value), String> {
    let md = fs::symlink_metadata(dir).map_err(|e| e.to_string())?;
    if !md.is_dir() || md.file_type().is_symlink() {
        return Err("adaptation directory must be a real directory".into());
    }
    let entries: BTreeSet<_> = fs::read_dir(dir)
        .map_err(|e| e.to_string())?
        .map(|e| e.map(|v| v.file_name().to_string_lossy().to_string()))
        .collect::<Result<_, _>>()
        .map_err(|e| e.to_string())?;
    if entries
        != FILES
            .iter()
            .map(|s| s.to_string())
            .chain(["manifest.json".into()])
            .collect()
    {
        return Err("adaptation file set mismatch".into());
    }
    let mut contents = BTreeMap::new();
    for name in FILES.into_iter().chain(["manifest.json"]) {
        let limit = match name {
            "original.comp" => SOURCE_LIMIT,
            "descriptor.json" | "manifest.json" => RECORD_LIMIT,
            _ => SPV_LIMIT,
        };
        contents.insert(name.to_string(), read(&dir.join(name), limit)?);
    }
    let summary = verify_snapshots(&contents)?;
    Ok((contents, summary))
}
pub fn verify_snapshots(contents: &BTreeMap<String, Vec<u8>>) -> Result<Value, String> {
    if contents.keys().map(String::as_str).collect::<BTreeSet<_>>()
        != FILES.into_iter().chain(["manifest.json"]).collect()
    {
        return Err("snapshot file set mismatch".into());
    }
    if contents["manifest.json"].len() as u64 > RECORD_LIMIT {
        return Err("manifest size limit exceeded".into());
    }
    let manifest: Manifest =
        serde_json::from_slice(&contents["manifest.json"]).map_err(|e| e.to_string())?;
    if manifest.schema_version != VERSION
        || manifest.state != "adapted_offline"
        || manifest.deployable
        || manifest.device_validation != "not_run"
        || manifest.semantic_equivalence != "not_run"
        || manifest.qualification != "not_run"
        || manifest.source_spirv_association != ASSOCIATION
    {
        return Err("adaptation status/qualification metadata invalid".into());
    }
    if manifest
        .files
        .keys()
        .map(String::as_str)
        .collect::<BTreeSet<_>>()
        != FILES.into_iter().collect()
    {
        return Err("manifest file set mismatch".into());
    }
    for name in FILES {
        let limit = match name {
            "original.comp" => SOURCE_LIMIT,
            "descriptor.json" => RECORD_LIMIT,
            _ => SPV_LIMIT,
        };
        let data = &contents[name];
        if data.len() as u64 > limit {
            return Err(format!("snapshot exceeds size limit: {name}"));
        }
        if manifest.files.get(name) != Some(&hash(data)) {
            return Err(format!("snapshot hash mismatch: {name}"));
        }
    }
    if hash(&contents["descriptor.json"]) != manifest.adaptation_id {
        return Err("adaptation descriptor ID mismatch".into());
    }
    let recorded: Descriptor =
        serde_json::from_slice(&contents["descriptor.json"]).map_err(|e| e.to_string())?;
    let (actual, adapted) = descriptor(&contents["original.comp"], &contents["original.spv"])?;
    if recorded != actual
        || pretty(&actual)? != contents["descriptor.json"]
        || adapted != contents["adapted.spv"]
    {
        return Err("deterministic adaptation replay mismatch".into());
    }
    Ok(
        json!({"adaptation_id":manifest.adaptation_id,"state":"verified_offline","deployable":false,"device_validation":"not_run","semantic_equivalence":"not_run","qualification":"not_run","source_spirv_association":ASSOCIATION,"descriptor":actual}),
    )
}
fn verify(dir: &Path) -> Result<Value, String> {
    let (_, mut summary) = snapshots(dir)?;
    summary["path"] = json!(dir);
    Ok(summary)
}
struct Stage(PathBuf);
impl Drop for Stage {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn freeze(source: &Path, spv: &Path, out: &Path) -> Result<Value, String> {
    // Transform the exact bounded snapshots; originals are never read again.
    let source = read(source, SOURCE_LIMIT)?;
    let original = read(spv, SPV_LIMIT)?;
    let (desc, adapted) = descriptor(&source, &original)?;
    let desc_bytes = pretty(&desc)?;
    let adaptation_id = hash(&desc_bytes);
    fs::create_dir_all(out).map_err(|e| e.to_string())?;
    let dest = out.join(format!("storage-{adaptation_id}"));
    if dest.exists() {
        let result = verify(&dest)?;
        if result["adaptation_id"] != adaptation_id {
            return Err("existing adaptation identity mismatch".into());
        }
        return Ok(result);
    }
    let stage = Stage(out.join(format!(
        ".adapting-{}-{}",
        std::process::id(),
        SEQ.fetch_add(1, Ordering::Relaxed)
    )));
    fs::create_dir(&stage.0).map_err(|e| e.to_string())?;
    let mut files = BTreeMap::new();
    for (name, data) in [
        ("original.comp", source),
        ("original.spv", original),
        ("adapted.spv", adapted),
        ("descriptor.json", desc_bytes),
    ] {
        fs::write(stage.0.join(name), &data).map_err(|e| e.to_string())?;
        files.insert(name.into(), hash(&data));
    }
    let manifest = Manifest {
        schema_version: VERSION,
        adaptation_id,
        state: "adapted_offline".into(),
        deployable: false,
        device_validation: "not_run".into(),
        semantic_equivalence: "not_run".into(),
        qualification: "not_run".into(),
        source_spirv_association: ASSOCIATION.into(),
        files,
    };
    fs::write(stage.0.join("manifest.json"), pretty(&manifest)?).map_err(|e| e.to_string())?;
    verify(&stage.0)?;
    fs::rename(&stage.0, &dest)
        .map_err(|e| format!("atomic adaptation publication failed: {e}"))?;
    verify(&dest)
}

pub fn cli(args: &[String]) -> Result<Value, String> {
    let command = args
        .first()
        .map(String::as_str)
        .ok_or("expected adaptation command")?;
    let allowed: &[&str] = match command {
        "adapt-storage" => &["--source", "--spirv", "--out"],
        "verify-adaptation" => &["--artifact"],
        _ => return Err("unknown adaptation command".into()),
    };
    let mut opts = BTreeMap::new();
    let mut index = 1;
    while index < args.len() {
        if args[index] == "--json" {
            index += 1;
            continue;
        }
        let key = args[index].as_str();
        if !allowed.contains(&key) {
            return Err(format!("unknown argument {key}"));
        }
        let val = args
            .get(index + 1)
            .filter(|v| !v.starts_with("--"))
            .ok_or_else(|| format!("missing value for {key}"))?;
        if opts.insert(key, val.as_str()).is_some() {
            return Err(format!("duplicate argument {key}"));
        }
        index += 2;
    }
    let get = |key| {
        opts.get(key)
            .copied()
            .map(Path::new)
            .ok_or_else(|| format!("missing {key}"))
    };
    match command {
        "adapt-storage" => freeze(get("--source")?, get("--spirv")?, get("--out")?),
        "verify-adaptation" => verify(get("--artifact")?),
        _ => unreachable!(),
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    pub const SOURCE: &str = include_str!("tests/fixtures/storage_sr.comp");
    pub fn naga_spirv(source: &str) -> Vec<u8> {
        let mut front = naga::front::glsl::Frontend::default();
        let module = front
            .parse(
                &naga::front::glsl::Options::from(naga::ShaderStage::Compute),
                source,
            )
            .unwrap();
        let info = naga::valid::Validator::new(
            naga::valid::ValidationFlags::all(),
            naga::valid::Capabilities::all(),
        )
        .validate(&module)
        .unwrap();
        naga::back::spv::write_vec(&module, &info, &naga::back::spv::Options::default(), None)
            .unwrap()
            .iter()
            .flat_map(|v| v.to_le_bytes())
            .collect()
    }
    fn mutated(f: impl FnOnce(&mut Module)) -> Result<(Vec<u8>, Changes, upopabi::Abi), String> {
        let mut m = parse(&naga_spirv(SOURCE)).unwrap();
        f(&mut m);
        adapt(&bytes(&m))
    }
    fn input_load(m: &Module) -> Instruction {
        let input = m
            .annotations
            .iter()
            .find(|i| {
                i.operands.get(1) == Some(&Operand::Decoration(Decoration::Binding))
                    && i.operands.get(2) == Some(&Operand::LiteralBit32(0))
            })
            .unwrap()
            .operands[0]
            .clone();
        m.all_inst_iter()
            .find(|i| i.class.opcode == Op::Load && i.operands.first() == Some(&input))
            .unwrap()
            .clone()
    }
    #[test]
    fn real_naga_sr_preserves_shared_output_and_other_instructions() {
        let original = naga_spirv(SOURCE);
        assert!(upopabi::reflect(&original).is_err());
        let before = parse(&original).unwrap();
        let (adapted, changes, _) = adapt(&original).unwrap();
        assert!(changes.output_shared_original_type);
        assert_eq!(changes.image_reads_to_fetch, 1);
        assert_eq!(changes.image_sizes_to_lod, 1);
        assert_eq!(changes.direct_loads, 2);
        assert_eq!(changes.unused_loads, 1);
        let after = parse(&adapted).unwrap();
        let loads: BTreeSet<_> = before
            .all_inst_iter()
            .filter(|i| {
                i.class.opcode == Op::Load
                    && i.operands.first() == Some(&Operand::IdRef(changes.input_variable))
            })
            .filter_map(|i| i.result_id)
            .collect();
        let changed: BTreeSet<_> = before
            .all_inst_iter()
            .filter(|i| {
                i.operands
                    .first()
                    .is_some_and(|o| matches!(o,Operand::IdRef(v) if loads.contains(v)))
                    && matches!(i.class.opcode, Op::ImageRead | Op::ImageQuerySize)
            })
            .filter_map(|i| i.result_id)
            .chain(loads.iter().copied())
            .chain([changes.input_variable])
            .collect();
        for i in before.all_inst_iter() {
            if i.result_id.is_some_and(|v| changed.contains(&v))
                || i.operands
                    == [
                        Operand::IdRef(changes.input_variable),
                        Operand::Decoration(Decoration::NonWritable),
                    ]
            {
                continue;
            }
            assert!(
                after.all_inst_iter().any(|a| a == i),
                "lost/changed original instruction {i:?}"
            );
        }
        assert_eq!(
            before
                .types_global_values
                .iter()
                .find(|i| i.result_id == Some(changes.original_image_type)),
            after
                .types_global_values
                .iter()
                .find(|i| i.result_id == Some(changes.original_image_type))
        );
        assert_eq!(
            before
                .types_global_values
                .iter()
                .find(|i| i.result_id == Some(changes.original_pointer_type)),
            after
                .types_global_values
                .iter()
                .find(|i| i.result_id == Some(changes.original_pointer_type))
        );
        for load in loads {
            let extracted = after
                .all_inst_iter()
                .find(|i| i.result_id == Some(load))
                .unwrap();
            assert_eq!(extracted.class.opcode, Op::Image);
        }
        assert_eq!(
            after
                .all_inst_iter()
                .filter(|i| i.class.opcode == Op::TypeInt
                    && i.operands == [Operand::LiteralBit32(32), Operand::LiteralBit32(1)])
                .count(),
            1,
            "do not redeclare existing nonaggregate types"
        );
        let mut declared = BTreeSet::new();
        for i in &after.types_global_values {
            if let Some(ty) = i.result_type {
                assert!(declared.contains(&ty), "type declared after use: {i:?}");
            }
            if let Some(v) = i.result_id {
                declared.insert(v);
            }
        }
        assert_eq!(adapt(&original).unwrap().0, adapted);
    }
    #[test]
    fn real_naga_interleaved_builtin_globals_keep_dependency_order() {
        fn deps(v: u32, defs: &BTreeMap<u32, Instruction>, needed: &mut BTreeSet<u32>) {
            if !needed.insert(v) {
                return;
            }
            let i = &defs[&v];
            for dep in i
                .result_type
                .into_iter()
                .chain(i.operands.iter().filter_map(|o| {
                    if let Operand::IdRef(v) = o {
                        Some(*v)
                    } else {
                        None
                    }
                }))
            {
                deps(dep, defs, needed);
            }
        }
        let mut m = parse(&naga_spirv(SOURCE)).unwrap();
        let builtin = m
            .types_global_values
            .iter()
            .find(|i| {
                i.class.opcode == Op::Variable
                    && i.operands.first() == Some(&Operand::StorageClass(StorageClass::Input))
            })
            .unwrap()
            .result_id
            .unwrap();
        let defs = m
            .types_global_values
            .iter()
            .filter_map(|i| i.result_id.map(|v| (v, i.clone())))
            .collect();
        let mut needed = BTreeSet::new();
        deps(builtin, &defs, &mut needed);
        let (prefix, others): (Vec<_>, Vec<_>) = std::mem::take(&mut m.types_global_values)
            .into_iter()
            .partition(|i| i.result_id.is_some_and(|v| needed.contains(&v)));
        m.types_global_values = prefix.into_iter().chain(others).collect();
        parse(&bytes(&m)).unwrap();
        let adapted = adapt(&bytes(&m)).unwrap().0;
        parse(&adapted).unwrap();
    }
    #[test]
    fn real_naga_variants_with_multiple_reads_are_transformed() {
        let source = SOURCE.replace("vec4 color = imageLoad(u_InputTexture, inPos);", "vec4 color = (imageLoad(u_InputTexture, inPos) + imageLoad(u_InputTexture, max(inPos-ivec2(1), ivec2(0)))) * 0.5;");
        let (_, changes, _) = adapt(&naga_spirv(&source)).unwrap();
        assert_eq!(changes.image_reads_to_fetch, 2);
    }
    #[test]
    fn rejects_pointer_and_loaded_resource_aliases() {
        for op in [Op::CopyObject, Op::Phi, Op::FunctionCall, Op::Select] {
            let error = mutated(|m| {
                let load = input_load(m);
                let new = m.header.as_ref().unwrap().bound;
                m.header.as_mut().unwrap().bound += 1;
                // Negative typed graph fixtures: unsupported consumer, irrespective of deadness.
                let operands = match op {
                    Op::CopyObject => vec![Operand::IdRef(load.result_id.unwrap())],
                    Op::Phi => vec![
                        Operand::IdRef(load.result_id.unwrap()),
                        Operand::IdRef(
                            m.functions[0].blocks[0]
                                .label
                                .as_ref()
                                .unwrap()
                                .result_id
                                .unwrap(),
                        ),
                    ],
                    Op::FunctionCall => vec![
                        Operand::IdRef(m.functions[0].def.as_ref().unwrap().result_id.unwrap()),
                        Operand::IdRef(load.result_id.unwrap()),
                    ],
                    _ => vec![
                        Operand::IdRef(load.result_id.unwrap()),
                        Operand::IdRef(load.result_id.unwrap()),
                        Operand::IdRef(load.result_id.unwrap()),
                    ],
                };
                m.functions[0].blocks[0].instructions.insert(
                    1,
                    Instruction::new(op, load.result_type, Some(new), operands),
                );
            })
            .unwrap_err();
            assert!(error.contains("unsupported"), "{op:?}: {error}");
        }
        assert!(mutated(|m| {
            let load = input_load(m);
            let var_id = id(load.operands.first()).unwrap();
            let ptr = m
                .types_global_values
                .iter()
                .find(|i| i.result_id == Some(var_id))
                .unwrap()
                .result_type;
            let new = m.header.as_ref().unwrap().bound;
            m.header.as_mut().unwrap().bound += 1;
            m.functions[0].blocks[0].instructions.insert(
                1,
                Instruction::new(Op::CopyObject, ptr, Some(new), vec![Operand::IdRef(var_id)]),
            );
        })
        .unwrap_err()
        .contains("pointer use"));
    }
    #[test]
    fn rejects_optional_masks_and_wrong_input_ops() {
        for op in [Op::ImageRead, Op::ImageFetch, Op::ImageQueryLevels] {
            assert!(mutated(|m| {
                let load = input_load(m).result_id.unwrap();
                let i = m
                    .all_inst_iter_mut()
                    .find(|i| {
                        i.class.opcode == Op::ImageRead
                            && i.operands.first() == Some(&Operand::IdRef(load))
                    })
                    .unwrap();
                if op == Op::ImageRead {
                    i.operands.push(Operand::ImageOperands(ImageOperands::NONE));
                } else {
                    *i = Instruction::new(
                        op,
                        i.result_type,
                        i.result_id,
                        if op == Op::ImageQueryLevels {
                            vec![Operand::IdRef(load)]
                        } else {
                            i.operands.clone()
                        },
                    );
                }
            })
            .is_err());
        }
    }
    #[test]
    fn rejects_abi_and_identity_corruption() {
        assert!(mutated(|m| {
            let var = id(input_load(m).operands.first()).unwrap();
            let index = m
                .types_global_values
                .iter()
                .position(|i| i.result_id == Some(var))
                .unwrap();
            let i = m.types_global_values.remove(index);
            m.functions[0].blocks[0].instructions.insert(0, i);
        })
        .unwrap_err()
        .contains("global variable"));
        assert!(mutated(|m| {
            let target = input_load(m).operands[0].clone();
            m.annotations.retain(|i| {
                i.operands != [target.clone(), Operand::Decoration(Decoration::NonWritable)]
            });
        })
        .is_err());
        assert!(mutated(|m| {
            let target = input_load(m).operands[0].clone();
            m.annotations.push(Instruction::new(
                Op::Decorate,
                None,
                None,
                vec![target, Operand::Decoration(Decoration::NonWritable)],
            ));
        })
        .unwrap_err()
        .contains("duplicate"));
        assert!(mutated(|m| m.execution_modes[0].operands[2] = Operand::LiteralBit32(8)).is_err());
        assert!(mutated(|m| m
            .annotations
            .iter_mut()
            .find(|i| i.operands.get(1) == Some(&Operand::Decoration(Decoration::DescriptorSet)))
            .unwrap()
            .operands[2] = Operand::LiteralBit32(1))
        .is_err());
        assert!(mutated(|m| {
            m.types_global_values.push(m.types_global_values[0].clone());
        })
        .is_err());
        assert!(mutated(|m| {
            let load = input_load(m).result_id.unwrap();
            m.all_inst_iter_mut()
                .find(|i| {
                    i.class.opcode == Op::ImageRead
                        && i.operands.first() == Some(&Operand::IdRef(load))
                })
                .unwrap()
                .operands[1] = Operand::IdRef(m.header.as_ref().unwrap().bound + 1);
        })
        .is_err());
        assert!(adapt(&[0; 20]).is_err());
        let original = naga_spirv(SOURCE);
        let mut dense = original[..20].to_vec();
        for _ in 0..=INSTRUCTION_LIMIT {
            dense.extend_from_slice(&(1u32 << 16).to_le_bytes());
        }
        assert!(adapt(&dense)
            .unwrap_err()
            .contains("instruction count limit"));
        assert!(cli(&[
            "adapt-storage".into(),
            "--source".into(),
            "x".into(),
            "--source".into(),
            "y".into()
        ])
        .is_err());
    }
}
