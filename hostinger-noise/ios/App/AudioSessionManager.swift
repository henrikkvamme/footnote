import AVFoundation

/// Audio session variants that Henrik can switch at runtime, to find one where our PiP card
/// and TikTok's sound both work.
enum AudioVariant: String, CaseIterable, Identifiable {
    case playbackMix = "playback + mixWithOthers"
    case playback = "playback (no mix)"
    case playAndRecordMix = "playAndRecord + mixWithOthers"
    case ambient = "ambient"
    case untouched = "do not touch the session"

    var id: String { rawValue }

    var mixes: Bool { self == .playbackMix || self == .playAndRecordMix || self == .ambient }
}

@MainActor
final class AudioSessionManager {
    private let engine = AVAudioEngine()
    private var sourceNode: AVAudioSourceNode?
    private(set) var silentAudioRunning = false
    /// Silent audio should run (it may be stopped for a moment by an interruption).
    private var wantSilent = false

    init() {
        let center = NotificationCenter.default
        center.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: .main) { [weak self] note in
            let type = (note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt).flatMap(AVAudioSession.InterruptionType.init) ?? .began
            let reason = (note.userInfo?[AVAudioSessionInterruptionReasonKey] as? UInt) ?? 0
            MainActor.assumeIsolated {
                log("audio interruption \(type == .began ? "began" : "ended") reason=\(reason)")
                // Keep the app alive: start the silent audio again when the interruption ends.
                if type == .ended, let self, self.wantSilent {
                    try? AVAudioSession.sharedInstance().setActive(true)
                    self.restartEngine()
                }
            }
        }
        center.addObserver(forName: AVAudioSession.silenceSecondaryAudioHintNotification, object: nil, queue: .main) { note in
            let type = (note.userInfo?[AVAudioSessionSilenceSecondaryAudioHintTypeKey] as? UInt) ?? 0
            MainActor.assumeIsolated { log("silence secondary audio hint: \(type == 1 ? "begin (other app plays)" : "end")") }
        }
        center.addObserver(forName: AVAudioSession.routeChangeNotification, object: nil, queue: .main) { note in
            let reason = (note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt) ?? 0
            MainActor.assumeIsolated { log("audio route change reason=\(reason)") }
        }
        center.addObserver(forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated {
                log("audio engine configuration change")
                if self?.silentAudioRunning == true { self?.restartEngine() }
            }
        }
    }

    /// Applies a variant. `forceMix` overrides the mix option (nil keeps the variant's own).
    func apply(_ variant: AudioVariant, forceMix: Bool? = nil) {
        let session = AVAudioSession.sharedInstance()
        let mix = forceMix ?? variant.mixes
        do {
            switch variant {
            case .playbackMix, .playback:
                try session.setCategory(.playback, mode: .moviePlayback, options: mix ? [.mixWithOthers] : [])
            case .playAndRecordMix:
                try session.setCategory(.playAndRecord, mode: .default, options: mix ? [.mixWithOthers, .defaultToSpeaker] : [.defaultToSpeaker])
            case .ambient:
                try session.setCategory(.ambient, mode: .default, options: [])
            case .untouched:
                log("audio session: left as is (\(describe()))")
                return
            }
            try session.setActive(true)
            log("audio session: \(describe())")
        } catch {
            log("audio session error for \(variant.rawValue) mix=\(mix): \(error.localizedDescription)")
        }
    }

    func deactivate() {
        do {
            try AVAudioSession.sharedInstance().setActive(false, options: [.notifyOthersOnDeactivation])
            log("audio session deactivated")
        } catch {
            log("audio session deactivate error: \(error.localizedDescription)")
        }
    }

    func describe() -> String {
        let s = AVAudioSession.sharedInstance()
        var options: [String] = []
        if s.categoryOptions.contains(.mixWithOthers) { options.append("mix") }
        if s.categoryOptions.contains(.duckOthers) { options.append("duck") }
        if s.categoryOptions.contains(.defaultToSpeaker) { options.append("speaker") }
        let category = s.category.rawValue.replacingOccurrences(of: "AVAudioSessionCategory", with: "")
        return "\(category)[\(options.joined(separator: ","))] mode=\(s.mode.rawValue.replacingOccurrences(of: "AVAudioSessionMode", with: ""))"
    }

    // MARK: Silent audio (keeps the app "playing", so iOS keeps it alive in the background)

    func setSilentAudio(_ on: Bool) {
        wantSilent = on
        if on { startSilentAudio() } else { stopSilentAudio() }
    }

    private func startSilentAudio() {
        guard !silentAudioRunning else { return }
        if sourceNode == nil {
            let format = engine.outputNode.inputFormat(forBus: 0)
            let node = AVAudioSourceNode { _, _, frameCount, audioBufferList -> OSStatus in
                for buffer in UnsafeMutableAudioBufferListPointer(audioBufferList) {
                    if let data = buffer.mData { memset(data, 0, Int(buffer.mDataByteSize)) }
                }
                _ = frameCount
                return noErr
            }
            engine.attach(node)
            engine.connect(node, to: engine.mainMixerNode, format: AVAudioFormat(standardFormatWithSampleRate: format.sampleRate > 0 ? format.sampleRate : 48_000, channels: 1))
            sourceNode = node
        }
        restartEngine()
    }

    private func restartEngine() {
        do {
            engine.prepare()
            try engine.start()
            silentAudioRunning = true
            log("silent audio running")
        } catch {
            silentAudioRunning = false
            log("silent audio error: \(error.localizedDescription)")
        }
    }

    private func stopSilentAudio() {
        guard silentAudioRunning else { return }
        engine.stop()
        silentAudioRunning = false
        log("silent audio stopped")
    }
}
