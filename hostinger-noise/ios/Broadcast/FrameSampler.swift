import Accelerate
import CoreMedia
import CoreVideo
import Foundation
import ImageIO
import ReplayKit
import UniformTypeIdentifiers
import Vision

/// Turns one ReplayKit screen buffer into a small grey frame, and the frame into a JPEG or OCR text.
///
/// Memory is the limit: a Broadcast Upload Extension is killed at about 50 MB. So the sampler reads only the
/// luma plane (Y of 420f/420v; text needs no colour), scales it straight to 540 px wide with vImage, and keeps
/// no reference to ReplayKit's buffer after `frame(from:)` returns. One frame is about 0.6 MB. No Core Image
/// context and no UIKit rendering. Compiled into the extension and, for the memory test, into the app.
final class FrameSampler {
    /// An upright 8-bit grey image.
    struct Frame {
        let width: Int
        let height: Int
        var pixels: [UInt8]
    }

    let targetWidth: Int

    init(targetWidth: Int = 540) {
        self.targetWidth = targetWidth
    }

    static func orientation(of sampleBuffer: CMSampleBuffer) -> CGImagePropertyOrientation {
        if let value = CMGetAttachment(sampleBuffer, key: RPVideoSampleOrientationKey as CFString, attachmentModeOut: nil) as? NSNumber,
           let orientation = CGImagePropertyOrientation(rawValue: value.uint32Value) {
            return orientation
        }
        return .up
    }

    // MARK: Pixel buffer to frame

    enum SampleError: Error, CustomStringConvertible {
        case format(OSType)
        case vImage(vImage_Error)

        var description: String {
            switch self {
            case .format(let type):
                let chars = [24, 16, 8, 0].map { Character(UnicodeScalar(UInt8((type >> $0) & 0xFF))) }
                return "pixel format \(String(chars)) is not supported"
            case .vImage(let code): return "vImage error \(code)"
            }
        }
    }

    /// Scales the screen to `targetWidth` (after rotation to upright) and returns the grey pixels.
    func frame(from pixelBuffer: CVPixelBuffer, orientation: CGImagePropertyOrientation) throws -> Frame {
        CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly) }

        let format = CVPixelBufferGetPixelFormatType(pixelBuffer)
        let sideways = [.left, .right, .leftMirrored, .rightMirrored].contains(orientation)

        var source: vImage_Buffer
        var bgraLuma: [UInt8] = []
        switch format {
        case kCVPixelFormatType_420YpCbCr8BiPlanarFullRange, kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange:
            source = vImage_Buffer(data: CVPixelBufferGetBaseAddressOfPlane(pixelBuffer, 0),
                                   height: vImagePixelCount(CVPixelBufferGetHeightOfPlane(pixelBuffer, 0)),
                                   width: vImagePixelCount(CVPixelBufferGetWidthOfPlane(pixelBuffer, 0)),
                                   rowBytes: CVPixelBufferGetBytesPerRowOfPlane(pixelBuffer, 0))
        case kCVPixelFormatType_32BGRA:
            // Not what ReplayKit sends today, but cheap to handle: BGRA to grey at full size (1 byte per pixel).
            let width = CVPixelBufferGetWidth(pixelBuffer)
            let height = CVPixelBufferGetHeight(pixelBuffer)
            var bgra = vImage_Buffer(data: CVPixelBufferGetBaseAddress(pixelBuffer), height: vImagePixelCount(height),
                                     width: vImagePixelCount(width), rowBytes: CVPixelBufferGetBytesPerRow(pixelBuffer))
            bgraLuma = [UInt8](repeating: 0, count: width * height)
            let error = bgraLuma.withUnsafeMutableBytes { raw -> vImage_Error in
                var grey = vImage_Buffer(data: raw.baseAddress, height: vImagePixelCount(height), width: vImagePixelCount(width), rowBytes: width)
                // BGRA order: weights for B, G, R, A (Rec. 601 luma), divisor 256.
                let matrix: [Int16] = [29, 150, 77, 0]
                return vImageMatrixMultiply_ARGB8888ToPlanar8(&bgra, &grey, matrix, 256, nil, 0, vImage_Flags(kvImageNoFlags))
            }
            guard error == kvImageNoError else { throw SampleError.vImage(error) }
            source = vImage_Buffer(data: nil, height: vImagePixelCount(height), width: vImagePixelCount(width), rowBytes: width)
        default:
            throw SampleError.format(format)
        }

        let uprightSourceWidth = sideways ? Int(source.height) : Int(source.width)
        let scale = min(1, Double(targetWidth) / Double(max(1, uprightSourceWidth)))
        let scaledWidth = max(1, Int((Double(source.width) * scale).rounded()))
        let scaledHeight = max(1, Int((Double(source.height) * scale).rounded()))
        var scaled = [UInt8](repeating: 0, count: scaledWidth * scaledHeight)

        let scaleError: vImage_Error = bgraLuma.withUnsafeMutableBytes { lumaRaw in
            if source.data == nil { source.data = lumaRaw.baseAddress }
            return scaled.withUnsafeMutableBytes { raw in
                var dest = vImage_Buffer(data: raw.baseAddress, height: vImagePixelCount(scaledHeight), width: vImagePixelCount(scaledWidth), rowBytes: scaledWidth)
                return vImageScale_Planar8(&source, &dest, nil, vImage_Flags(kvImageNoFlags))
            }
        }
        guard scaleError == kvImageNoError else { throw SampleError.vImage(scaleError) }
        bgraLuma = []

        let rotation: Int
        switch orientation {
        case .down, .downMirrored: rotation = kRotate180DegreesClockwise
        case .right, .rightMirrored: rotation = kRotate90DegreesClockwise
        case .left, .leftMirrored: rotation = kRotate90DegreesCounterClockwise
        default: return Frame(width: scaledWidth, height: scaledHeight, pixels: scaled)
        }
        let outWidth = sideways ? scaledHeight : scaledWidth
        let outHeight = sideways ? scaledWidth : scaledHeight
        var rotated = [UInt8](repeating: 0, count: outWidth * outHeight)
        let rotateError: vImage_Error = scaled.withUnsafeMutableBytes { inRaw in
            rotated.withUnsafeMutableBytes { outRaw in
                var src = vImage_Buffer(data: inRaw.baseAddress, height: vImagePixelCount(scaledHeight), width: vImagePixelCount(scaledWidth), rowBytes: scaledWidth)
                var dst = vImage_Buffer(data: outRaw.baseAddress, height: vImagePixelCount(outHeight), width: vImagePixelCount(outWidth), rowBytes: outWidth)
                return vImageRotate90_Planar8(&src, &dst, UInt8(rotation), 0, vImage_Flags(kvImageNoFlags))
            }
        }
        guard rotateError == kvImageNoError else { throw SampleError.vImage(rotateError) }
        return Frame(width: outWidth, height: outHeight, pixels: rotated)
    }

    // MARK: Change detection

    /// A 16 x 32 grid of mean grey levels, to tell a changed screen from an unchanged one cheaply.
    static func signature(of frame: Frame) -> [UInt8] {
        let cols = 16, rows = 32
        var sums = [Int](repeating: 0, count: cols * rows)
        var counts = [Int](repeating: 0, count: cols * rows)
        frame.pixels.withUnsafeBufferPointer { p in
            var y = 0
            while y < frame.height {
                let row = min(rows - 1, y * rows / frame.height)
                var x = 0
                while x < frame.width {
                    let cell = row * cols + min(cols - 1, x * cols / frame.width)
                    sums[cell] += Int(p[y * frame.width + x])
                    counts[cell] += 1
                    x += 3
                }
                y += 3
            }
        }
        return zip(sums, counts).map { UInt8($0 / max(1, $1)) }
    }

    /// Same screen: the grids differ little on average and in every cell. The cell check keeps a new subtitle
    /// line (a small area) from being taken for the same screen.
    static func isSame(_ a: [UInt8], _ b: [UInt8]) -> Bool {
        guard a.count == b.count, !a.isEmpty else { return false }
        var total = 0
        var most = 0
        for i in 0..<a.count {
            let d = abs(Int(a[i]) - Int(b[i]))
            total += d
            most = max(most, d)
        }
        return Double(total) / Double(a.count) < 2 && most < 16
    }

    // MARK: Output

    private static func cgImage(_ frame: Frame, _ body: (CGImage) -> Void) {
        frame.pixels.withUnsafeBytes { raw in
            guard let base = raw.baseAddress,
                  let provider = CGDataProvider(dataInfo: nil, data: base, size: raw.count, releaseData: { _, _, _ in }),
                  let image = CGImage(width: frame.width, height: frame.height, bitsPerComponent: 8, bitsPerPixel: 8, bytesPerRow: frame.width,
                                      space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue),
                                      provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent)
            else { return }
            body(image)
        }
    }

    /// A grey JPEG with ImageIO. About 40 to 60 KB for a 540 x 1174 TikTok frame at quality 0.5.
    static func jpeg(_ frame: Frame, quality: Double = 0.5) -> Data? {
        let data = NSMutableData()
        var ok = false
        cgImage(frame) { image in
            guard let dest = CGImageDestinationCreateWithData(data, UTType.jpeg.identifier as CFString, 1, nil) else { return }
            CGImageDestinationAddImage(dest, image, [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary)
            ok = CGImageDestinationFinalize(dest)
        }
        return ok ? data as Data : nil
    }

    struct TextLine: Encodable {
        let text: String
        let x: Double
        let y: Double
        let w: Double
        let h: Double
        let confidence: Double
    }

    /// Vision OCR on the grey frame. Boxes are 0 to 1 with the origin at the top left.
    static func recognize(_ frame: Frame, accurate: Bool) throws -> [TextLine] {
        var lines: [TextLine] = []
        var failure: Error?
        cgImage(frame) { image in
            let request = VNRecognizeTextRequest()
            request.recognitionLevel = accurate ? .accurate : .fast
            request.usesLanguageCorrection = accurate
            if accurate { request.automaticallyDetectsLanguage = true }
            request.minimumTextHeight = 0.012
            let handler = VNImageRequestHandler(cgImage: image, orientation: .up, options: [:])
            do {
                try handler.perform([request])
                for observation in request.results ?? [] {
                    guard let candidate = observation.topCandidates(1).first else { continue }
                    let box = observation.boundingBox
                    lines.append(TextLine(text: candidate.string, x: round3(box.minX), y: round3(1 - box.maxY), w: round3(box.width), h: round3(box.height),
                                          confidence: round3(Double(candidate.confidence))))
                }
            } catch {
                failure = error
            }
        }
        if let failure { throw failure }
        return lines
    }

    private static func round3(_ v: Double) -> Double { (v * 1000).rounded() / 1000 }
    private static func round3(_ v: CGFloat) -> Double { round3(Double(v)) }
}
