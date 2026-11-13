// CutSkills · 人物遮罩 / 人脸框：两个模式，同一个二进制。
//
// 为什么用代码抠像而不是"找个录屏软件绿幕"：这支参考片是**实拍口播**，
// 人物和背景没有色差可用，只能靠语义分割。macOS 自带 Vision 就能做，
// 不需要下载任何模型（这条对「依赖只装一次、别重复下」的要求很关键）。
//
// 用法：
//   person_mask <video> <start_s> <dur_s> <outdir> [fps]        # 人物遮罩（旧调用，一个字没动）
//   person_mask --faces <video> <start_s> <dur_s> [fps]         # 人脸框，JSON 打到 stdout
// 产出（遮罩模式）：outdir/f0000.png …（灰度图，白=人）
// 产出（人脸模式）：[{"t":0.0,"x":0.31,"y":0.18,"w":0.22,"h":0.19,"conf":0.98}, …]
//   坐标归一化到 [0,1]，原点左上（Vision 给的是原点左下，这里已经翻过）。
//   t 是**相对起点 start_s 的秒数**，不是素材绝对时间。
import AVFoundation
import CoreImage
import Foundation
import ImageIO
import UniformTypeIdentifiers
import Vision

let args = CommandLine.arguments

/// 建一个从 startS 起读的 AVAssetReader，返回 (reader, output, 源帧率, 显示变换)。
/// 两个模式共用——遮罩和人脸要读的必须是**同一段**解码路径，不然帧序对不上。
/// 显示变换（preferredTransform）必须一路带出去：AVAssetReader 吐的是**存储方向**的
/// 像素，竖屏素材存成 1920×1080 + 旋转 90°，不转就检出一张横着的脸（实测：人像跑到了
/// 画面左侧一半的外面，框住的是背景与人左肩——这种错很安静，只会让卡的位置算错）。
func makeReader(_ videoPath: String, _ startS: Double, _ durS: Double)
    -> (AVAssetReader, AVAssetReaderTrackOutput, Double, CGAffineTransform)? {
    let asset = AVURLAsset(url: URL(fileURLWithPath: videoPath))
    guard let track = asset.tracks(withMediaType: .video).first else { return nil }
    let natural = Double(track.nominalFrameRate > 0 ? track.nominalFrameRate : 30)
    guard let reader = try? AVAssetReader(asset: asset) else { return nil }
    reader.timeRange = CMTimeRange(start: CMTime(seconds: startS, preferredTimescale: 600),
                                   duration: CMTime(seconds: durS, preferredTimescale: 600))
    let settings: [String: Any] = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
    let output = AVAssetReaderTrackOutput(track: track, outputSettings: settings)
    output.alwaysCopiesSampleData = false
    reader.add(output)
    reader.startReading()
    return (reader, output, natural, track.preferredTransform)
}

/// 把一帧转成**显示方向**（与播放器里看到的一致）。已经是正的直接原样返回。
/// 关键点：`preferredTransform` 定义在**原点左上、y 向下**的视频合成坐标系里，
/// 而 CIImage 是 y 向上。直接把 t 套给 CIImage 会得到一张**倒着的帧**（实测：
/// 人脸跑到画面 67% 高度处，坐标全错但不报错）。所以要把它共轭到 CI 坐标系：
/// T' = F ∘ t ∘ F，F 是关于 y=H/2 的翻转。
func oriented(_ pb: CVPixelBuffer, _ t: CGAffineTransform, _ ctx: CIContext) -> CVPixelBuffer? {
    if t.isIdentity { return pb }
    let hh = CGFloat(CVPixelBufferGetHeight(pb))
    let flip = CGAffineTransform(a: 1, b: 0, c: 0, d: -1, tx: 0, ty: hh)
    var ci = CIImage(cvPixelBuffer: pb).transformed(by: flip.concatenating(t).concatenating(flip))
    let e = ci.extent
    ci = ci.transformed(by: CGAffineTransform(translationX: -e.origin.x, y: -e.origin.y))
    let w = Int(ci.extent.width.rounded()), h = Int(ci.extent.height.rounded())
    guard w > 0, h > 0 else { return nil }
    let attrs: [String: Any] = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
    var out: CVPixelBuffer?
    guard CVPixelBufferCreate(kCFAllocatorDefault, w, h, kCVPixelFormatType_32BGRA,
                              attrs as CFDictionary, &out) == kCVReturnSuccess,
          let dst = out else { return nil }
    ctx.render(ci, to: dst, bounds: CGRect(x: 0, y: 0, width: w, height: h),
               colorSpace: CGColorSpaceCreateDeviceRGB())
    return dst
}

/// 跳帧用**整数**步长。用浮点（natural/fps）在 30.000002/30 = 1.00000006 这种情况下
/// 会变成"每帧都该跳"，一帧都不产出、还报 status=completed——这种失败不报错，最难查。
func frameStep(_ natural: Double, _ fps: Double) -> Int {
    return max(1, Int((natural / fps).rounded()))
}

// ------------------------------------------------------------------ 人脸模式
// 为什么要单独的一个人脸通道：MG 卡的合法叠加态判的是「卡有没有压脸」，
// 而人物遮罩只能回答"人占多大面积"，回答不了"眼睛和鼻梁在哪一格像素里"。
// VNDetectFaceRectanglesRequest 就是那个能回答的最小工具，系统自带、不下载权重。
if args.count >= 2 && args[1] == "--faces" {
    guard args.count >= 5 else {
        FileHandle.standardError.write(
            "用法：person_mask --faces <video> <start> <dur> [fps]\n".data(using: .utf8)!)
        exit(2)
    }
    let videoPath = args[2]
    let startS = Double(args[3]) ?? 0
    let durS = Double(args[4]) ?? 1
    // 默认 2 fps：布局只要知道「这张卡在的这几秒，脸在哪个框里」，
    // 30 fps 采一模一样的框是白烧 CPU。
    let fps = args.count > 5 ? (Double(args[5]) ?? 2) : 2
    let dumpPath = ProcessInfo.processInfo.environment["PM_DUMP"]
    guard let (_, output, natural, tf) = makeReader(videoPath, startS, durS) else { exit(3) }
    let step = frameStep(natural, fps)
    let req = VNDetectFaceRectanglesRequest()
    let ctx = CIContext()
    var rows: [String] = []
    var fi = 0          // 源帧序号（跳帧前）
    var kept = 0        // 实际送检的帧数
    var missed = 0      // 送检但一帧人脸都没有
    while let sb = output.copyNextSampleBuffer() {
        defer { fi += 1 }
        if step > 1 && (fi % step) != 0 { continue }
        guard let pb = CMSampleBufferGetImageBuffer(sb) else { continue }
        guard let upright = oriented(pb, tf, ctx) else { continue }
        if let dp = dumpPath, fi == 0 {
            // 调试用：把“送去检测的那一帧”原样落盘，坐标不对时先看这一张
            var ci = CIImage(cvPixelBuffer: upright)
            let e = ci.extent
            ci = ci.transformed(by: CGAffineTransform(translationX: -e.origin.x, y: -e.origin.y))
            if let cg = ctx.createCGImage(ci, from: CGRect(x: 0, y: 0, width: e.width, height: e.height)),
               let d = CGImageDestinationCreateWithURL(URL(fileURLWithPath: dp) as CFURL,
                                                       UTType.png.identifier as CFString, 1, nil) {
                CGImageDestinationAddImage(d, cg, nil)
                CGImageDestinationFinalize(d)
            }
        }
        let t = Double(fi) / natural
        kept += 1
        let handler = VNImageRequestHandler(cvPixelBuffer: upright, options: [:])
        do { try handler.perform([req]) } catch { missed += 1; continue }
        guard let obs = req.results, !obs.isEmpty else { missed += 1; continue }
        for o in obs {
            let bb = o.boundingBox          // 归一化，原点左下
            let x = Double(bb.origin.x)
            let w = Double(bb.size.width)
            let h = Double(bb.size.height)
            let y = 1.0 - Double(bb.origin.y) - h   // 翻成原点左上
            let f = { (v: Double) in String(format: "%.4f", v) }
            rows.append("{\"t\":\(String(format: "%.3f", t)),\"x\":\(f(x)),\"y\":\(f(y))"
                        + ",\"w\":\(f(w)),\"h\":\(f(h))"
                        + ",\"conf\":\(String(format: "%.3f", Double(o.confidence)))}")
        }
    }
    print("[" + rows.joined(separator: ",") + "]")
    FileHandle.standardError.write(
        "faces=\(rows.count) frames=\(kept) noface=\(missed) fps=\(fps) src_fps=\(natural)\n"
            .data(using: .utf8)!)
    exit(0)
}

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

guard let (reader, output, natural, _) = makeReader(videoPath, startS, durS) else { exit(3) }

let ctx = CIContext()
let req = VNGeneratePersonSegmentationRequest()
req.qualityLevel = .accurate
req.outputPixelFormat = kCVPixelFormatType_OneComponent8

var n = 0
let step = frameStep(natural, fps)
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
