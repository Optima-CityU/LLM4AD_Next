// ocr.swift — 截图文字坐标备胎(UI树为空时的第二感知通道)
// macOS 自带 Vision,离线免费。UI树拿不到文字坐标的屏(游戏/自绘界面/dump失败),
// 用它从截图里识别文字+方框,agent照样能"点名"点击。
// 编译: swiftc -O ocr.swift -o ocr    用法: ./ocr <图片路径>
// 输出: 每行一个 JSON {"t":"文字","b":[x1,y1,x2,y2]} (图片像素,左上原点)
import Foundation
import Vision
import AppKit

guard CommandLine.arguments.count > 1,
      let img = NSImage(contentsOfFile: CommandLine.arguments[1]),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("ocr: 读不了图\n".data(using: .utf8)!)
    exit(1)
}

let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.recognitionLanguages = ["zh-Hans", "en-US"]
req.usesLanguageCorrection = true
let handler = VNImageRequestHandler(cgImage: cg, options: [:])
try? handler.perform([req])

var out = ""
for obs in (req.results ?? []) {
    guard let cand = obs.topCandidates(1).first, cand.confidence >= 0.3 else { continue }
    let t = cand.string.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !t.isEmpty else { continue }
    // Vision 归一化框(左下原点) → 图片像素(左上原点)
    let bb = obs.boundingBox, w = CGFloat(cg.width), h = CGFloat(cg.height)
    let x1 = Int((bb.origin.x * w).rounded())
    let y1 = Int(((1 - bb.origin.y - bb.height) * h).rounded())
    let x2 = Int(((bb.origin.x + bb.width) * w).rounded())
    let y2 = Int(((1 - bb.origin.y) * h).rounded())
    let esc = t.replacingOccurrences(of: "\\", with: "\\\\")
               .replacingOccurrences(of: "\"", with: "\\\"")
    out += "{\"t\":\"\(esc)\",\"b\":[\(x1),\(y1),\(x2),\(y2)]}\n"
}
FileHandle.standardOutput.write(out.data(using: .utf8)!)
