// phonefarm 决策侧车 —— 把 Core ML 决策模型接进 `phonefarm decide --sidecar`。
//
// 协议(与 `https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/SPEC_DECISION_PATH.md` §4 一致, 一行一个 JSON):
//
//   → {"op":"hello","protocol":1}
//   ← {"op":"ready","protocol":1,"model":"…","units":"…"}
//   → {"op":"decide","state":"…","questions":[…]}
//   ← {"op":"answers","answers":[…]}
//   ← {"op":"error","message":"…"}      (模型侧单次失败, 会话仍然可用)
//
// 编译:
//   swiftc -O -o build/decision-sidecar sidecar.swift -framework CoreML -framework Foundation
//
// 用法:
//   decision-sidecar --model <模型.mlmodelc|.mlmodel> [--units cpu|ane|all]
//   decision-sidecar --selftest --model <…>      # 跑一条内置请求并打点, 用来核实环境
//
// ─────────────────────────────────────────────────────────────────────────
// 关于这份实现到底算了什么 —— 说清楚, 免得被当成模型质量
//
// **它是一台「词面相关度打分器」, 不是 Laya, 也不是任何训练过的模型。**
//
// 模型本体(reference-model.py 生成)只有一个矩阵乘: `scores[i] = dot(state, options[i])`。
// 所以整条链路的语义完全由两端的编码器决定, 而这里用的编码器是:
//
//   文本 → 小写 → 字符三元组(trigram) → FNV-1a 哈希到 64 维桶 → L2 归一化
//
// 于是 `dot(state, option)` 就是「状态文本与选项文本在字符三元组空间里的余弦相似度」。
// 这对「选项 label 的词出现在 state 里」这类问题是真有信号的(远好于随机),
// 但它不认识语义: 换个说法、换个语言、指代与蕴涵, 它全都不懂。
//
// **要拿到 Laya 那样的质量, 换掉的是两端**: 把 trigram 编码器换成真正的
// transformer 编码器(或者在模型里加 encoder 层), 决策头的形状不变。
// 那份权重不随本仓库分发 —— 见 README.md。
// ─────────────────────────────────────────────────────────────────────────

import CoreML
import Foundation

let PROTOCOL = 1
/// 与 reference-model.py 生成的模型一致
let DIM = 256
let MAX_OPTIONS = 32

// ───────────────────────── 编码器(词面) ─────────────────────────

/// FNV-1a 64 位。选它是因为短、无依赖、跨语言好复现。
func fnv1a(_ bytes: [UInt8]) -> UInt64 {
    var h: UInt64 = 0xcbf2_9ce4_8422_2325
    for b in bytes {
        h ^= UInt64(b)
        h = h &* 0x0000_0100_0000_01b3
    }
    return h
}

/// 文本 → 64 维单位向量。见文件头: 这是词面编码器, 不是语义编码器。
///
/// 用**字符二元组**而不是三元组: 中文短词(如「退款」)在两段不同上下文里
/// (状态里的「要退款」vs 选项里的「退款」)只有二元组能对上 —— 三元组要三字对齐,
/// 实测会把「想要退款」判成「想要技术支持」。
func encode(_ text: String) -> [Float] {
    var v = [Double](repeating: 0, count: DIM)
    let cs = Array(text.lowercased().unicodeScalars)
    guard cs.count > 0 else { return v.map { Float($0) } }
    if cs.count < 2 {
        let h = fnv1a(Array(String(cs[0]).utf8))
        v[Int(h % UInt64(DIM))] += 1
    } else {
        for i in 0..<(cs.count - 1) {
            let bi = String(String.UnicodeScalarView(cs[i...i + 1]))
            let h = fnv1a(Array(bi.utf8))
            // 非负计数哈希(标准 hashing vectorizer): 共享的 n-gram 只会把相似度抬高。
            // 不用带符号随机投影 —— 那个在短文本上碰撞噪声会压过信号。
            v[Int(h % UInt64(DIM))] += 1
        }
    }
    let norm = sqrt(v.reduce(0) { $0 + $1 * $1 })
    if norm > 0 { for i in 0..<DIM { v[i] /= norm } }
    return v.map { Float($0) }
}

// ───────────────────────── softmax ─────────────────────────

func softmax(_ xs: [Double]) -> [Double] {
    guard let m = xs.max(), xs.count > 0 else { return [] }
    let exps = xs.map { exp($0 - m) }
    let s = exps.reduce(0, +)
    return s > 0 ? exps.map { $0 / s } : exps
}

func sigmoid(_ x: Double) -> Double { 1.0 / (1.0 + exp(-x)) }

// ───────────────────────── 模型 ─────────────────────────

final class DecisionModel {
    let model: MLModel
    let name: String
    let units: String

    init(path: String, units: MLComputeUnits) throws {
        let cfg = MLModelConfiguration()
        cfg.computeUnits = units
        let url = URL(fileURLWithPath: path)
        // .mlmodel 会自动编译(慢); .mlmodelc 直接用
        if path.hasSuffix(".mlmodelc") {
            self.model = try MLModel(contentsOf: url, configuration: cfg)
        } else {
            let compiled = try MLModel.compileModel(at: url)
            self.model = try MLModel(contentsOf: compiled, configuration: cfg)
        }
        self.name = url.lastPathComponent
        switch units {
        case .cpuOnly: self.units = "cpu"
        case .cpuAndGPU: self.units = "cpu+gpu"
        case .all: self.units = "all(含 ANE)"
        default: self.units = "cpu+ane"
        }
    }

    /// `scores[i] = dot(state, options[i])`, i 只取前 n 个有效选项。
    func score(state: [Float], options: [[Float]], count: Int) throws -> [Double] {
        guard count > 0, count <= MAX_OPTIONS else {
            throw NSError(domain: "sidecar", code: 2,
                          userInfo: [NSLocalizedDescriptionKey: "选项数 \(count) 超出模型上限 \(MAX_OPTIONS)"])
        }
        guard state.count == DIM else {
            throw NSError(domain: "sidecar", code: 3,
                          userInfo: [NSLocalizedDescriptionKey: "state 维度 \(state.count) ≠ \(DIM)"])
        }
        let st = try MLMultiArray(shape: [NSNumber(value: DIM)], dataType: .float32)
        for j in 0..<DIM { st[j] = NSNumber(value: state[j]) }

        let op = try MLMultiArray(shape: [NSNumber(value: MAX_OPTIONS), NSNumber(value: DIM)],
                                  dataType: .float32)
        for i in 0..<MAX_OPTIONS {
            let row = i < options.count ? options[i] : [Float](repeating: 0, count: DIM)
            for j in 0..<DIM { op[[NSNumber(value: i), NSNumber(value: j)]] = NSNumber(value: row[j]) }
        }

        let input = try MLDictionaryFeatureProvider(dictionary: [
            "state": MLFeatureValue(multiArray: st),
            "options": MLFeatureValue(multiArray: op),
        ])
        let out = try model.prediction(from: input)
        guard let arr = out.featureValue(for: "scores")?.multiArrayValue else {
            throw NSError(domain: "sidecar", code: 4,
                          userInfo: [NSLocalizedDescriptionKey: "模型没有输出 scores"])
        }
        return (0..<count).map { Double(truncating: arr[$0]) }
    }
}

// ───────────────────────── 解码三种 typed decisions ─────────────────────────

func answer(_ q: [String: Any], model: DecisionModel) throws -> [String: Any] {
    let id = q["id"] as? String ?? ""
    let type = q["type"] as? String ?? ""
    let instructions = q["instructions"] as? String ?? ""
    let stateText = (q["_state"] as? String) ?? ""

    switch type {
    case "choice":
        let opts = q["options"] as? [[String: Any]] ?? []
        guard opts.count >= 2 else {
            throw NSError(domain: "sidecar", code: 5,
                          userInfo: [NSLocalizedDescriptionKey: "\(id): choice 至少要两个选项"])
        }
        let labels = opts.map { $0["label"] as? String ?? "" }
        let vectors = opts.map { o -> [Float] in
            let desc = o["description"] as? String ?? ""
            return encode("\(instructions) \(o["label"] as? String ?? "") \(desc)")
        }
        let scores = try model.score(state: encode(stateText), options: vectors, count: opts.count)
        let probs = softmax(scores)
        var best = 0
        for i in 1..<probs.count where probs[i] > probs[best] { best = i }
        return ["type": "choice", "id": id, "selected": labels[best], "p": probs[best]]

    case "score":
        let levels = q["levels"] as? [String] ?? []
        guard levels.count >= 2 else {
            throw NSError(domain: "sidecar", code: 6,
                          userInfo: [NSLocalizedDescriptionKey: "\(id): score 至少要两个档位"])
        }
        let vectors = levels.map { encode("\(instructions) \($0)") }
        let scores = try model.score(state: encode(stateText), options: vectors, count: levels.count)
        let probs = softmax(scores)
        var expected = 0.0
        for (i, p) in probs.enumerated() {
            // 档位是有序的: 期望档位索引 / (K-1) → 0~1
            expected += Double(i) / Double(levels.count - 1) * p
        }
        return ["type": "score", "id": id, "expected": expected]

    case "noul":
        // 是/否: 拿「肯定表述」与「否定表述」两个方向比, 而不是只看指令与状态相关不相关。
        let yes = encode("\(instructions) 是 会 应该 需要 支持")
        let no = encode("\(instructions) 否 不会 不应该 不需 反对")
        let s = try model.score(state: encode(stateText), options: [yes, no], count: 2)
        let d = s[0] - s[1]
        return ["type": "noul", "id": id, "p": sigmoid(3.0 * d)]

    default:
        throw NSError(domain: "sidecar", code: 7,
                      userInfo: [NSLocalizedDescriptionKey: "\(id): 未知题型 '\(type)'"])
    }
}

// ───────────────────────── 主循环 ─────────────────────────

func emit(_ obj: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: obj),
          var line = String(data: data, encoding: .utf8) else { return }
    line += "\n"
    FileHandle.standardOutput.write(line.data(using: .utf8)!)
}

func fail(_ message: String) {
    emit(["op": "error", "message": message])
}

func main() -> Int32 {
    var modelPath: String? = ProcessInfo.processInfo.environment["PHONEFARM_DECISION_MODEL"]
    var units: MLComputeUnits = .all
    var selftest = false
    var probe = false
    var args = Array(CommandLine.arguments.dropFirst())
    while !args.isEmpty {
        let a = args.removeFirst()
        switch a {
        case "--model":
            modelPath = args.isEmpty ? nil : args.removeFirst()
        case "--units":
            let v = args.isEmpty ? "all" : args.removeFirst()
            switch v {
            case "cpu": units = .cpuOnly
            case "gpu": units = .cpuAndGPU
            case "ane": units = .cpuAndNeuralEngine
            default: units = .all
            }
        case "--selftest":
            selftest = true
        case "--probe":
            probe = true
        case "--help", "-h":
            FileHandle.standardError.write(
                "用法: decision-sidecar --model <模型> [--units cpu|gpu|ane|all] [--selftest] [--probe]\n"
                    .data(using: .utf8)!)
            return 2
        default:
            FileHandle.standardError.write("未知参数 \(a)\n".data(using: .utf8)!)
            return 2
        }
    }

    guard let path = modelPath, !path.isEmpty else {
        FileHandle.standardError.write(
            "必须给 --model 或设 PHONEFARM_DECISION_MODEL。模型从哪来见 https://github.com/HGamey/docs/blob/main/repos/phonefarm/tools/decision-sidecar/README.md\n"
                .data(using: .utf8)!)
        return 2
    }

    let t0 = Date()
    let model: DecisionModel
    do {
        model = try DecisionModel(path: path, units: units)
    } catch {
        FileHandle.standardError.write("加载模型失败: \(error)\n".data(using: .utf8)!)
        return 2
    }
    let loadMs = Date().timeIntervalSince(t0) * 1000

    // ── --probe: 用受控输入核实模型图确实在算 s_i = dot(state, options_i) ──
    // 模型图和编码器是两件事。上面那次「想要退款被判成技术支持」曾被误归到编码器头上,
    // 实际上得先证明图本身算对了 —— 这个模式就是干这个的。
    if probe {
        var e0 = [Float](repeating: 0, count: DIM)
        e0[0] = 1
        var neg = [Float](repeating: 0, count: DIM)
        neg[0] = -1
        var zeros = [Float](repeating: 0, count: DIM)
        zeros[1] = 1
        let s = (try? model.score(state: e0, options: [e0, neg, zeros], count: 3)) ?? []
        print("model: \(model.name)  units: \(model.units)")
        print("probe scores: \(s.map { String(format: "%.4f", $0) }.joined(separator: ", "))")
        // 期望: [1, -1, 0]
        let want: [Double] = [1, -1, 0]
        var ok = s.count == 3
        if ok {
            for i in 0..<3 where abs(s[i] - want[i]) > 1e-3 { ok = false }
        }
        print(ok ? "✓ 模型图正确: s_i = dot(state, options_i)"
                 : "✗ 模型图不对 —— 期望 [1, -1, 0]; 检查 reference-model.py 的乘/求和轴")
        return ok ? 0 : 1
    }

    if selftest {
        let questions: [[String: Any]] = [
            ["type": "choice", "id": "want", "instructions": "客户想要什么?",
             "options": [["label": "refund", "description": "退款"],
                         ["label": "order status", "description": "查单"],
                         ["label": "technical help", "description": "技术支持"]]],
            ["type": "score", "id": "anger", "instructions": "客户有多生气?",
             "levels": ["calm", "annoyed", "angry"]],
            ["type": "noul", "id": "churn", "instructions": "客户会不会流失?"],
        ]
        let state = "客户为订单 #4471 被重复扣款, 没有人回复。今天就要退款。"
        print("model: \(model.name)  units: \(model.units)  加载 \(String(format: "%.0f", loadMs))ms")
        for q in questions {
            var qq = q
            qq["_state"] = state
            let t = Date()
            do {
                let a = try answer(qq, model: model)
                let ms = Date().timeIntervalSince(t) * 1000
                let data = try JSONSerialization.data(withJSONObject: a, options: [.sortedKeys])
                print("  \(String(format: "%6.2f", ms))ms  \(String(data: data, encoding: .utf8)!)")
            } catch {
                print("  失败: \(error.localizedDescription)")
                return 1
            }
        }
        return 0
    }

    // ── 一行一个 JSON 的主循环 ──
    while let line = readLine(strippingNewline: true) {
        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { continue }
        guard let data = trimmed.data(using: .utf8),
              let msg = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let op = msg["op"] as? String else {
            fail("不是合法 JSON 或没有 op")
            continue
        }
        switch op {
        case "hello":
            let p = msg["protocol"] as? Int ?? 0
            if p != PROTOCOL {
                emit(["op": "ready", "protocol": PROTOCOL, "model": model.name,
                      "units": model.units, "warning": "客户端提的是协议 \(p)"])
            } else {
                emit(["op": "ready", "protocol": PROTOCOL, "model": model.name, "units": model.units])
            }
        case "decide":
            let state = msg["state"] as? String ?? ""
            let questions = msg["questions"] as? [[String: Any]] ?? []
            var answers: [[String: Any]] = []
            var failure: String? = nil
            for q in questions {
                var qq = q
                qq["_state"] = state
                do {
                    answers.append(try answer(qq, model: model))
                } catch {
                    // 模型侧的单次失败: 报错但不杀会话 —— 与 FluidUse 的桥接层同语义
                    failure = error.localizedDescription
                    break
                }
            }
            if let f = failure {
                fail(f)
            } else {
                emit(["op": "answers", "answers": answers])
            }
        default:
            fail("未知 op '\(op)'")
        }
    }
    return 0
}

exit(main())
