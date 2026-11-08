// CutSkills · 人物遮罩：用 macOS Vision 的 VNGeneratePersonSegmentationRequest 逐帧出 mask。
//
// 为什么用代码抠像而不是"找个录屏软件绿幕"：这支参考片是**实拍口播**，
// 人物和背景没有色差可用，只能靠语义分割。macOS 自带 Vision 就能做，
// 不需要下载任何模型（这条对「依赖只装一次、别重复下」的要求很关键）。
//
// 用法：person_mask <video> <start_s> <dur_s> <outdir> [fps]
// 产出：outdir/f0000.png …（灰度图，白=人）
import AVFoundation
import CoreImage
import Foundation
import ImageIO
import UniformTypeIdentifiers
import Vision

let args = CommandLine.arguments
guard args.count >= 5 else {
    FileHandle.standardError.write("用法：person_mask <video> <start> <dur> <outdir> [fps]\n".data(using: .utf8)!)
    exit(2)
}
let videoPath = args[1]
let startS = Double(args[2]) ?? 0
let durS = Double(args[3]) ?? 1
let outDir = URL(fileURLWithPath: args[4])
let fps = args.count > 5 ? (Double(args[5]) ?? 30) : 30
try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)

let asset = AVURLAsset(url: URL(fileURLWithPath: videoPath))
guard let track = asset.tracks(withMediaType: .video).first else { exit(3) }
let natural = Double(track.nominalFrameRate > 0 ? track.nominalFrameRate : 30)

let reader = try AVAssetReader(asset: asset)
reader.timeRange = CMTimeRange(start: CMTime(seconds: startS, preferredTimescale: 600),
                               duration: CMTime(seconds: durS, preferredTimescale: 600))
let settings: [String: Any] = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
let output = AVAssetReaderTrackOutput(track: track, outputSettings: settings)
output.alwaysCopiesSampleData = false
reader.add(output)
reader.startReading()

let ctx = CIContext()
let req = VNGeneratePersonSegmentationRequest()
req.qualityLevel = .accurate
req.outputPixelFormat = kCVPixelFormatType_OneComponent8

var n = 0
// 跳帧用**整数**步长。用浮点（natural/fps）在 30.000002/30 = 1.00000006 这种情况下
// 会变成"每帧都该跳"，一帧都不产出、还报 status=completed——这种失败不报错，最难查。
let step = max(1, Int((natural / fps).rounded()))
var fi = 0
while let sb = output.copyNextSampleBuffer() {
    defer { fi += 1 }
    if step > 1 && fi % step != 0 { continue }
    guard let pb = CMSampleBufferGetImageBuffer(sb) else { continue }
    let handler = VNImageRequestHandler(cvPixelBuffer: pb, options: [:])
    do { try handler.perform([req]) }
    catch {
        if n == 0 { FileHandle.standardError.write("perform 失败：\(error)\n".data(using: .utf8)!) }
        continue
    }
    guard let obs = req.results?.first else {
        if n == 0 { FileHandle.standardError.write("没有分割结果（这一帧没找到人）\n".data(using: .utf8)!) }
        continue
    }
    let maskPB = obs.pixelBuffer
    // mask 是低分辨率的（Vision 自己决定），先缩放到原帧尺寸再存
    let w = CVPixelBufferGetWidth(pb), h = CVPixelBufferGetHeight(pb)
    var maskCI = CIImage(cvPixelBuffer: maskPB)
    let sx = CGFloat(w) / maskCI.extent.width
    let sy = CGFloat(h) / maskCI.extent.height
    maskCI = maskCI.transformed(by: CGAffineTransform(scaleX: sx, y: sy))
    guard let cg = ctx.createCGImage(maskCI, from: CGRect(x: 0, y: 0, width: w, height: h)) else { continue }
    let url = outDir.appendingPathComponent(String(format: "f%04d.png", n))
    guard let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil)
    else { continue }
    CGImageDestinationAddImage(dest, cg, nil)
    CGImageDestinationFinalize(dest)
    n += 1
}
print("masks=\(n) fps=\(fps) src_fps=\(natural) status=\(reader.status.rawValue) err=\(String(describing: reader.error))")
