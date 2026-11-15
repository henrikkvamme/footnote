import CoreVideo
import Foundation
import ImageIO
import VideoToolbox

/// Measures the extension's screen pipeline (FrameSampler) on one real TikTok screenshot (BenchFrame.jpg,
/// 1206 x 2622 like an iPhone 17 Pro screen), converted to the 420f format that ReplayKit sends.
///
/// The extension cannot run in the simulator, so this runs the same code in the app process and reports the
/// extra memory each step needs (the peak footprint during the step minus the footprint before it) and the time.
/// On a phone the numbers in the extension heartbeat (memoryMB, memoryPeakMB) are the real ones.
enum ScreenBenchmark {
    struct Step {
        let name: String
        let ms: Double
        let extraMB: Double
        let detail: String

        var line: String { String(format: "%@: %.0f ms, +%.1f MB peak%@", name, ms, extraMB, detail.isEmpty ? "" : ", " + detail) }
    }

    static func run(rounds: Int = 5) -> [String] {
        guard let url = Bundle.main.url(forResource: "BenchFrame", withExtension: "jpg"),
              let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil),
              let pixelBuffer = makePixelBuffer(image) else {
            return ["benchmark: could not load BenchFrame.jpg as a 420f pixel buffer"]
        }
        let sampler = FrameSampler(targetWidth: 540)
        var out = ["benchmark: \(CVPixelBufferGetWidth(pixelBuffer))x\(CVPixelBufferGetHeight(pixelBuffer)) 420f, \(rounds) rounds, footprint before \(ProcessStats.memoryMB()) MB"]
        var frame: FrameSampler.Frame?

        out.append(measure("scale to grey 540 px", rounds) {
            frame = try? sampler.frame(from: pixelBuffer, orientation: .up)
            return frame.map { "\($0.width)x\($0.height)" } ?? "failed"
        }.line)
        guard let frame else { return out }

        out.append(measure("change check (16x32 grid)", rounds) {
            _ = FrameSampler.isSame(FrameSampler.signature(of: frame), FrameSampler.signature(of: frame))
            return ""
        }.line)
        out.append(measure("S1 JPEG q0.5", rounds) {
            let data = FrameSampler.jpeg(frame, quality: 0.5)
            return "\((data?.count ?? 0) / 1024) KB"
        }.line)
        var sample = ""
        out.append(measure("S2 OCR fast", rounds) {
            let lines = (try? FrameSampler.recognize(frame, accurate: false)) ?? []
            sample = lines.prefix(6).map(\.text).joined(separator: " | ")
            return "\(lines.count) lines"
        }.line)
        out.append("OCR fast text: \(sample)")
        out.append(measure("S2 OCR accurate", rounds) {
            let lines = (try? FrameSampler.recognize(frame, accurate: true)) ?? []
            sample = lines.prefix(6).map(\.text).joined(separator: " | ")
            return "\(lines.count) lines"
        }.line)
        out.append("OCR accurate text: \(sample)")
        out.append("benchmark: footprint after \(ProcessStats.memoryMB()) MB")
        return out
    }

    /// Runs `body` `rounds` times. Samples the footprint each millisecond on another thread for the peak.
    private static func measure(_ name: String, _ rounds: Int, _ body: () -> String) -> Step {
        let before = ProcessStats.memoryMB()
        let lock = NSLock()
        var peak = before
        var running = true
        let sampler = Thread {
            while lock.withLock({ running }) {
                let now = ProcessStats.memoryMB()
                lock.withLock { peak = max(peak, now) }
                usleep(1000)
            }
        }
        sampler.start()
        var detail = ""
        let start = Date()
        for _ in 0..<rounds {
            autoreleasepool { detail = body() }
        }
        let ms = Date().timeIntervalSince(start) * 1000 / Double(rounds)
        lock.withLock { running = false }
        let after = ProcessStats.memoryMB()
        let extra = max(0, lock.withLock { max(peak, after) } - before)
        return Step(name: name, ms: ms, extraMB: (extra * 10).rounded() / 10, detail: detail)
    }

    /// The screenshot as a 420f (bi-planar, full range) pixel buffer, like a ReplayKit screen buffer.
    private static func makePixelBuffer(_ image: CGImage) -> CVPixelBuffer? {
        let width = image.width, height = image.height
        let attrs = [kCVPixelBufferIOSurfacePropertiesKey: [:]] as CFDictionary
        var bgra: CVPixelBuffer?
        var yuv: CVPixelBuffer?
        guard CVPixelBufferCreate(nil, width, height, kCVPixelFormatType_32BGRA, attrs, &bgra) == kCVReturnSuccess,
              CVPixelBufferCreate(nil, width, height, kCVPixelFormatType_420YpCbCr8BiPlanarFullRange, attrs, &yuv) == kCVReturnSuccess,
              let bgra, let yuv else { return nil }
        CVPixelBufferLockBaseAddress(bgra, [])
        let context = CGContext(data: CVPixelBufferGetBaseAddress(bgra), width: width, height: height, bitsPerComponent: 8,
                                bytesPerRow: CVPixelBufferGetBytesPerRow(bgra), space: CGColorSpaceCreateDeviceRGB(),
                                bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)
        context?.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        CVPixelBufferUnlockBaseAddress(bgra, [])
        var session: VTPixelTransferSession?
        guard VTPixelTransferSessionCreate(allocator: nil, pixelTransferSessionOut: &session) == noErr, let session else { return nil }
        defer { VTPixelTransferSessionInvalidate(session) }
        return VTPixelTransferSessionTransferImage(session, from: bgra, to: yuv) == noErr ? yuv : nil
    }
}
