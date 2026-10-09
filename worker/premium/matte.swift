// Personen-Maske je Bild (Apple Vision, läuft lokal). Aufruf: matte <video> <zielordner> <start_s> <ende_s>
import AVFoundation
import Vision
import CoreImage

let a = CommandLine.arguments
guard a.count >= 5, let t0 = Double(a[3]), let t1 = Double(a[4]) else { print("usage: matte video outdir start end"); exit(2) }
let asset = AVURLAsset(url: URL(fileURLWithPath: a[1]))
let outDir = URL(fileURLWithPath: a[2])
try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
let sem = DispatchSemaphore(value: 0)
var track: AVAssetTrack?
Task { track = try? await asset.loadTracks(withMediaType: .video).first; sem.signal() }
sem.wait()
guard let tr = track, let reader = try? AVAssetReader(asset: asset) else { print("kein Video"); exit(1) }
let out = AVAssetReaderTrackOutput(track: tr, outputSettings: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
reader.add(out)
reader.timeRange = CMTimeRange(start: CMTime(seconds: t0, preferredTimescale: 600), end: CMTime(seconds: t1, preferredTimescale: 600))
reader.startReading()
let ctx = CIContext()
let req = VNGeneratePersonSegmentationRequest()
req.qualityLevel = .accurate
req.outputPixelFormat = kCVPixelFormatType_OneComponent8
var n = 0
while let sb = out.copyNextSampleBuffer() {
    guard let pb = CMSampleBufferGetImageBuffer(sb) else { continue }
    let w = CGFloat(CVPixelBufferGetWidth(pb)), h = CGFloat(CVPixelBufferGetHeight(pb))
    let handler = VNImageRequestHandler(cvPixelBuffer: pb, orientation: .up, options: [:])
    try? handler.perform([req])
    var img = CIImage(color: .black).cropped(to: CGRect(x: 0, y: 0, width: w, height: h))
    if let m = req.results?.first?.pixelBuffer {
        let ci = CIImage(cvPixelBuffer: m)
        img = ci.transformed(by: CGAffineTransform(scaleX: w / ci.extent.width, y: h / ci.extent.height))
    }
    let url = outDir.appendingPathComponent(String(format: "%05d.png", n))
    try? ctx.writePNGRepresentation(of: img, to: url, format: .L8, colorSpace: CGColorSpaceCreateDeviceGray())
    n += 1
}
print("Masken:", n)
