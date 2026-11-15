import CoreMedia
import CoreVideo
import UIKit

/// What the PiP card shows.
struct CardContent: Equatable {
    enum Mode: Equatable { case waiting, listening, found }
    var mode: Mode
    var label: String
    var text: String
    var detail: String
}

/// Draws the caption card into a pixel buffer and wraps it in a CMSampleBuffer for AVSampleBufferDisplayLayer.
enum CardRenderer {
    /// About 16:5, a caption strip.
    static let size = CGSize(width: 960, height: 300)

    static func tint(_ mode: CardContent.Mode) -> UIColor {
        switch mode {
        case .waiting: return UIColor(white: 0.62, alpha: 1)
        case .listening: return UIColor(red: 0.36, green: 0.92, blue: 0.78, alpha: 1)
        case .found: return UIColor(red: 1.0, green: 0.68, blue: 0.22, alpha: 1)
        }
    }

    static func sampleBuffer(for content: CardContent) -> CMSampleBuffer? {
        guard let pixelBuffer = pixelBuffer(for: content) else { return nil }
        var format: CMVideoFormatDescription?
        CMVideoFormatDescriptionCreateForImageBuffer(allocator: nil, imageBuffer: pixelBuffer, formatDescriptionOut: &format)
        guard let format else { return nil }
        var timing = CMSampleTimingInfo(
            duration: CMTime(value: 1, timescale: 2),
            presentationTimeStamp: CMClockGetTime(CMClockGetHostTimeClock()),
            decodeTimeStamp: .invalid)
        var sample: CMSampleBuffer?
        CMSampleBufferCreateReadyWithImageBuffer(
            allocator: nil, imageBuffer: pixelBuffer, formatDescription: format, sampleTiming: &timing, sampleBufferOut: &sample)
        guard let sample else { return nil }
        if let attachments = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: true), CFArrayGetCount(attachments) > 0 {
            let dict = unsafeBitCast(CFArrayGetValueAtIndex(attachments, 0), to: CFMutableDictionary.self)
            CFDictionarySetValue(
                dict,
                Unmanaged.passUnretained(kCMSampleAttachmentKey_DisplayImmediately).toOpaque(),
                Unmanaged.passUnretained(kCFBooleanTrue).toOpaque())
        }
        return sample
    }

    static func pixelBuffer(for content: CardContent) -> CVPixelBuffer? {
        let width = Int(size.width), height = Int(size.height)
        let attributes: [CFString: Any] = [
            kCVPixelBufferCGImageCompatibilityKey: true,
            kCVPixelBufferCGBitmapContextCompatibilityKey: true,
            kCVPixelBufferIOSurfacePropertiesKey: [:] as CFDictionary,
        ]
        var buffer: CVPixelBuffer?
        CVPixelBufferCreate(nil, width, height, kCVPixelFormatType_32BGRA, attributes as CFDictionary, &buffer)
        guard let buffer else { return nil }
        CVPixelBufferLockBaseAddress(buffer, [])
        defer { CVPixelBufferUnlockBaseAddress(buffer, []) }
        guard let context = CGContext(
            data: CVPixelBufferGetBaseAddress(buffer), width: width, height: height, bitsPerComponent: 8,
            bytesPerRow: CVPixelBufferGetBytesPerRow(buffer), space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)
        else { return nil }

        // UIKit drawing expects a top-left origin.
        context.translateBy(x: 0, y: CGFloat(height))
        context.scaleBy(x: 1, y: -1)
        UIGraphicsPushContext(context)
        defer { UIGraphicsPopContext() }
        draw(content, in: CGRect(origin: .zero, size: size))
        return buffer
    }

    static func draw(_ content: CardContent, in rect: CGRect) {
        let tint = tint(content.mode)
        UIColor(red: 0.055, green: 0.055, blue: 0.07, alpha: 1).setFill()
        UIRectFill(rect)
        if content.mode == .found {
            tint.withAlphaComponent(0.14).setFill()
            UIRectFill(rect)
        }
        // Accent bar.
        tint.setFill()
        UIBezierPath(roundedRect: CGRect(x: 28, y: 36, width: 10, height: rect.height - 72), cornerRadius: 5).fill()

        let left: CGFloat = 64
        let width = rect.width - left - 36
        let label = NSAttributedString(string: content.label.uppercased(), attributes: [
            .font: UIFont.systemFont(ofSize: 30, weight: .heavy),
            .foregroundColor: tint,
            .kern: 2,
        ])
        label.draw(in: CGRect(x: left, y: 30, width: width * 0.7, height: 40))

        let detail = NSAttributedString(string: content.detail, attributes: [
            .font: UIFont.monospacedDigitSystemFont(ofSize: 24, weight: .semibold),
            .foregroundColor: UIColor(white: 1, alpha: 0.5),
        ])
        let detailSize = detail.size()
        detail.draw(at: CGPoint(x: rect.width - 36 - detailSize.width, y: 34))

        let paragraph = NSMutableParagraphStyle()
        paragraph.lineBreakMode = .byTruncatingHead
        paragraph.lineSpacing = 2
        let textRect = CGRect(x: left, y: 84, width: width, height: rect.height - 84 - 24)
        var fontSize: CGFloat = 54
        var text = NSAttributedString()
        // Shrink the font until the text fits in two or three lines.
        while fontSize >= 34 {
            text = NSAttributedString(string: content.text, attributes: [
                .font: UIFont.systemFont(ofSize: fontSize, weight: .bold),
                .foregroundColor: UIColor.white,
                .paragraphStyle: paragraph,
            ])
            let needed = text.boundingRect(with: CGSize(width: width, height: .greatestFiniteMagnitude), options: [.usesLineFragmentOrigin], context: nil)
            if needed.height <= textRect.height { break }
            fontSize -= 4
        }
        text.draw(with: textRect, options: [.usesLineFragmentOrigin, .truncatesLastVisibleLine], context: nil)
    }
}
