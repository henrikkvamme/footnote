import AudioToolbox
import CoreMedia
import Foundation

/// Turns ReplayKit audio sample buffers into 16 kHz mono 16-bit PCM.
/// ReplayKit app audio often arrives as big-endian interleaved Int16 at 44.1 or 48 kHz, but the format
/// can change, so this reads the stream description of every buffer and handles Int16, Int32 and Float32,
/// either endianness, interleaved or not.
struct AudioDownmixer {
    let outRate: Double = 16_000

    private(set) var formatDescription = ""
    private var inRate: Double = 0
    private var position: Double = 0
    private var last: Float = 0
    private var lowPass: Float = 0

    /// Returns mono Float samples at the input rate, for level metering.
    static func mono(from sampleBuffer: CMSampleBuffer) -> (samples: [Float], asbd: AudioStreamBasicDescription)? {
        guard let format = CMSampleBufferGetFormatDescription(sampleBuffer),
              let asbdPointer = CMAudioFormatDescriptionGetStreamBasicDescription(format) else { return nil }
        let asbd = asbdPointer.pointee
        guard asbd.mFormatID == kAudioFormatLinearPCM else { return nil }

        var sizeNeeded = 0
        CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer, bufferListSizeNeededOut: &sizeNeeded, bufferListOut: nil, bufferListSize: 0,
            blockBufferAllocator: nil, blockBufferMemoryAllocator: nil, flags: 0, blockBufferOut: nil)
        guard sizeNeeded > 0 else { return nil }
        let raw = UnsafeMutableRawPointer.allocate(byteCount: sizeNeeded, alignment: MemoryLayout<AudioBufferList>.alignment)
        defer { raw.deallocate() }
        let list = raw.bindMemory(to: AudioBufferList.self, capacity: 1)
        var block: CMBlockBuffer?
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer, bufferListSizeNeededOut: nil, bufferListOut: list, bufferListSize: sizeNeeded,
            blockBufferAllocator: nil, blockBufferMemoryAllocator: nil,
            flags: kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment, blockBufferOut: &block)
        guard status == noErr else { return nil }

        let flags = asbd.mFormatFlags
        let isFloat = flags & kAudioFormatFlagIsFloat != 0
        let bigEndian = flags & kAudioFormatFlagIsBigEndian != 0
        let nonInterleaved = flags & kAudioFormatFlagIsNonInterleaved != 0
        let bits = Int(asbd.mBitsPerChannel)
        let bytesPerSample = max(1, bits / 8)
        let frames = CMSampleBufferGetNumSamples(sampleBuffer)
        guard frames > 0 else { return nil }

        var mono = [Float](repeating: 0, count: frames)
        var channelCount = 0
        for buffer in UnsafeMutableAudioBufferListPointer(list) {
            guard let data = buffer.mData else { continue }
            let perFrame = nonInterleaved ? 1 : max(1, Int(buffer.mNumberChannels))
            let usable = min(frames, Int(buffer.mDataByteSize) / (bytesPerSample * perFrame))
            for c in 0..<perFrame {
                for f in 0..<usable {
                    let index = f * perFrame + c
                    let sample: Float
                    switch (isFloat, bits) {
                    case (true, 32):
                        var v = data.loadUnaligned(fromByteOffset: index * 4, as: UInt32.self)
                        if bigEndian { v = v.byteSwapped }
                        sample = Float(bitPattern: v)
                    case (false, 16):
                        var v = data.loadUnaligned(fromByteOffset: index * 2, as: Int16.self)
                        if bigEndian { v = v.byteSwapped }
                        sample = Float(v) / 32_768
                    case (false, 32):
                        var v = data.loadUnaligned(fromByteOffset: index * 4, as: Int32.self)
                        if bigEndian { v = v.byteSwapped }
                        sample = Float(v) / 2_147_483_648
                    default:
                        sample = 0
                    }
                    mono[f] += sample
                }
                channelCount += 1
            }
        }
        if channelCount > 1 {
            let scale = 1 / Float(channelCount)
            for i in mono.indices { mono[i] *= scale }
        }
        return (mono, asbd)
    }

    static func describe(_ asbd: AudioStreamBasicDescription) -> String {
        let flags = asbd.mFormatFlags
        let kind = flags & kAudioFormatFlagIsFloat != 0 ? "float" : "int"
        let endian = flags & kAudioFormatFlagIsBigEndian != 0 ? "BE" : "LE"
        let layout = flags & kAudioFormatFlagIsNonInterleaved != 0 ? "planar" : "interleaved"
        return "\(Int(asbd.mSampleRate)) Hz, \(asbd.mChannelsPerFrame) ch, \(kind)\(asbd.mBitsPerChannel) \(endian) \(layout)"
    }

    /// Low-pass, then linear resampling to 16 kHz. Keeps state across buffers.
    mutating func resample(_ x: [Float], inputRate: Double) -> [Int16] {
        guard !x.isEmpty, inputRate > 0 else { return [] }
        if inputRate != inRate {
            inRate = inputRate
            position = 0
            last = 0
            lowPass = 0
        }
        // One-pole low-pass at about 7 kHz against aliasing. Cheap and good enough for speech.
        let alpha = Float(1 - exp(-2 * Double.pi * 7_000 / inputRate))
        var filtered = x
        var y = lowPass
        for i in filtered.indices {
            y += alpha * (filtered[i] - y)
            filtered[i] = y
        }
        lowPass = y

        let step = inputRate / outRate
        var out: [Int16] = []
        out.reserveCapacity(Int(Double(x.count) / step) + 2)
        let n = filtered.count
        while position < Double(n - 1) {
            let i = Int(position.rounded(.down))
            let a = i < 0 ? last : filtered[i]
            let b = filtered[i + 1]
            let frac = Float(position - Double(i))
            let v = max(-1, min(1, a + (b - a) * frac))
            out.append(Int16(v * 32_767))
            position += step
        }
        position -= Double(n)
        last = filtered[n - 1]
        return out
    }
}
