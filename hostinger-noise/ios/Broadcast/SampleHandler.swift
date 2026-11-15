import ActivityKit
import CoreMedia
import QuartzCore
import Foundation
import ReplayKit

/// Broadcast Upload Extension. Takes the app audio (TikTok's sound), cuts it into chunks of 3 to 8 s at a quiet
/// point, and sends them in order as 16 kHz mono mu-law to the server. It polls the server each second for the
/// Live Activity state and, when the update path allows it, updates the app's Live Activity itself (path A).
/// It also reads the screen: at most `fps` frames per second (default 1) become a small grey frame; an
/// unchanged screen is skipped; the frame goes to the server as a JPEG (S1) or as on-device OCR text (S2).
/// Every other video buffer is dropped at once without touching its pixels. The extension memory limit is
/// about 50 MB, so the heartbeat reports the footprint and its peak.
/// A heartbeat with counters goes to the server each 2 s.
final class SampleHandler: RPBroadcastSampleHandler {
    private let lock = NSLock()
    private let queue = DispatchQueue(label: "lens.broadcast.work")
    private var timer: DispatchSourceTimer?
    private var pollTimer: DispatchSourceTimer?
    private var downmixer = AudioDownmixer()
    private let uploader = ChunkUploader()
    private let sink = LiveActivitySink(source: "extension")
    private let sampler = FrameSampler(targetWidth: 540)
    private let frameUploader = FrameUploader()
    private let frameQueue = DispatchQueue(label: "lens.broadcast.frame", qos: .utility)

    private var state = "created"
    private var startedAt = Date()
    private var videoCount = 0
    private var audioAppCount = 0
    private var audioMicCount = 0
    private var audioAppSeconds: Double = 0
    private var pending: [Int16] = []
    private var chunkSeq = 0
    private var silentSkipped = 0
    private var windowSquares: Double = 0
    private var windowSamples = 0
    private var lastRMS: Double = 0
    private var maxRMS: Double = 0
    private var micRMS: Double = 0
    private var format = ""
    private var logLines: [String] = []

    // Screen reading.
    private var screenPath: ScreenPath = .jpeg
    private var fps: Double = 1
    private var lastFrameAt: CFTimeInterval = 0
    private var frameBusy = false
    private var frameSeq = 0
    private var lastSignature: [UInt8] = []
    private var framesTaken = 0
    private var framesSame = 0
    private var frameErrors = 0
    private var lastFrameMs = 0
    private var lastFrameKB = 0.0
    private var lastFrameError = ""
    private var ocrMs = 0
    private var ocrLines = 0
    private var memoryPeak = 0.0
    private var frameSize = ""

    // Settings from the server (the app sets them).
    private var chunkSeconds: Double = 4
    private var updatePath: UpdatePath = .auto
    private var alerts = true
    /// Technique notes on the Island (Debug). Off by default: Footnote shows only checks, which the app drives.
    private var techniqueNotes = false
    private var alertSound = false
    private var pathActive = "starting"
    private var activitiesVisible = -1
    private var polls = 0
    private var pollFailures = 0
    private var pollInFlight = false
    private var lastResultId = 0
    /// Until then the app shows a "Footnote this" answer: do not update the Live Activity from here.
    private var holdUntil: CFTimeInterval = 0

    // On-device speech (iOS 26, OnDeviceSpeech): the phone transcribes and sends only text to POST /api/text.
    private var speechPath: SpeechPath = .upload
    private var speechEngine: AnyObject?
    private var speechLang: String?
    private var speechStarting = false
    private var speechNote = "upload"
    private var speechStartedAt: CFTimeInterval = 0
    private var speechBlockedUntil: CFTimeInterval = 0
    private var loudAt: CFTimeInterval = 0
    private var lastFinalAt: CFTimeInterval = 0
    private var lastVolatileAt: CFTimeInterval = 0
    private var currentVideo: Int?
    private var videoLangs: [String] = []
    private var textsSent = 0
    private var textFailures = 0
    private var lastTextMs = 0
    private let textSession: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 10
        config.waitsForConnectivity = false
        return URLSession(configuration: config)
    }()

    private let rate = 16_000
    /// A chunk with an RMS below this is digital silence (TikTok paused): do not send it.
    private let silenceRMS = 0.0003

    private let pollSession: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 4
        config.timeoutIntervalForResource = 6
        config.waitsForConnectivity = false
        return URLSession(configuration: config)
    }()

    // MARK: Broadcast lifecycle

    override func broadcastStarted(withSetupInfo setupInfo: [String: NSObject]?) {
        lock.lock()
        state = "running"
        startedAt = Date()
        lock.unlock()
        uploader.log = { [weak self] in self?.log($0) }
        frameUploader.log = { [weak self] in self?.log($0) }
        frameUploader.onNotRead = { [weak self] in
            guard let self else { return }
            self.lock.withLock { self.lastSignature = [] }
        }
        let visible = LiveActivitySink.visible.count
        lock.withLock { activitiesVisible = visible }
        log("broadcast started; Live Activities visible from the extension: \(visible) (all states: \(Activity<LensActivityAttributes>.activities.count))")
        observeHold()
        startTimers()
        sendHeartbeat()
        BroadcastSignal.started.post()
    }

    /// The app posts `holdIsland` when "Footnote this" shows its answer, and its speech path when the broadcast
    /// starts or the setting changes (Darwin notifications, no App Group).
    private func observeHold() {
        let observer = Unmanaged.passUnretained(self).toOpaque()
        for signal in [BroadcastSignal.holdIsland, .speechUpload, .speechOnDevice, .speechAuto, .techniqueOn, .techniqueOff] {
            CFNotificationCenterAddObserver(CFNotificationCenterGetDarwinNotifyCenter(), observer, { _, observer, name, _, _ in
                guard let observer, let name else { return }
                let handler = Unmanaged<SampleHandler>.fromOpaque(observer).takeUnretainedValue()
                handler.signal(name.rawValue as String)
            }, signal.rawValue as CFString, nil, .deliverImmediately)
        }
    }

    private func signal(_ raw: String) {
        if raw == BroadcastSignal.holdIsland.rawValue {
            lock.withLock { holdUntil = CACurrentMediaTime() + 12 }
            sink.reset()
        } else if raw == BroadcastSignal.techniqueOn.rawValue || raw == BroadcastSignal.techniqueOff.rawValue {
            lock.withLock { techniqueNotes = raw == BroadcastSignal.techniqueOn.rawValue }
        } else if let path = SpeechPath(signal: raw) {
            queue.async { self.setSpeechPath(path) }
        }
    }

    override func broadcastPaused() {
        setState("paused")
        log("broadcast paused")
    }

    override func broadcastResumed() {
        setState("running")
        log("broadcast resumed")
    }

    override func broadcastFinished() {
        setState("finished")
        log("broadcast finished")
        BroadcastSignal.finished.post()
        CFNotificationCenterRemoveEveryObserver(CFNotificationCenterGetDarwinNotifyCenter(), Unmanaged.passUnretained(self).toOpaque())
        stopSpeech("broadcast finished")
        flushChunk(force: true)
        sendHeartbeat()
        timer?.cancel()
        pollTimer?.cancel()
    }

    // MARK: Samples

    override func processSampleBuffer(_ sampleBuffer: CMSampleBuffer, with sampleBufferType: RPSampleBufferType) {
        switch sampleBufferType {
        case .video:
            handleVideo(sampleBuffer)
        case .audioApp:
            handleAppAudio(sampleBuffer)
        case .audioMic:
            let level = AudioDownmixer.mono(from: sampleBuffer).map { Self.rms($0.samples) } ?? 0
            lock.lock(); audioMicCount += 1; micRMS = level; lock.unlock()
        @unknown default:
            break
        }
    }

    private func handleAppAudio(_ sampleBuffer: CMSampleBuffer) {
        guard let (samples, asbd) = AudioDownmixer.mono(from: sampleBuffer) else {
            lock.lock(); audioAppCount += 1; lock.unlock()
            return
        }
        let description = AudioDownmixer.describe(asbd)
        var squares: Double = 0
        for s in samples { squares += Double(s * s) }

        lock.lock()
        audioAppCount += 1
        audioAppSeconds += Double(samples.count) / max(1, asbd.mSampleRate)
        windowSquares += squares
        windowSamples += samples.count
        let formatChanged = description != format
        format = description
        let resampled = downmixer.resample(samples, inputRate: asbd.mSampleRate)
        let engine = speechEngine
        if engine == nil { pending.append(contentsOf: resampled) }
        if samples.count > 0, squares / Double(samples.count) > 0.003 * 0.003 { loudAt = CACurrentMediaTime() }
        let full = engine == nil && pending.count >= Int(chunkSeconds * Double(rate))
        lock.unlock()

        if formatChanged { log("audioApp format: \(description)") }
        if full { flushChunk(force: false) }
        if let engine, #available(iOS 26.0, *), let speech = engine as? OnDeviceSpeech {
            // On the phone: the sound goes to the recognizer, not to the server.
            speech.feed(resampled)
        }
    }

    private static func rms(_ samples: [Float]) -> Double {
        guard !samples.isEmpty else { return 0 }
        var squares: Double = 0
        for s in samples { squares += Double(s * s) }
        return (squares / Double(samples.count)).squareRoot()
    }

    // MARK: Screen frames

    /// Called for every screen buffer (about 50 per second). Most return at the first check.
    private func handleVideo(_ sampleBuffer: CMSampleBuffer) {
        let now = CACurrentMediaTime()
        lock.lock()
        videoCount += 1
        let path = screenPath
        guard path != .off, !frameBusy, now - lastFrameAt >= 1 / max(0.25, fps) else { lock.unlock(); return }
        lastFrameAt = now
        frameBusy = true
        framesTaken += 1
        frameSeq += 1
        let seq = frameSeq
        lock.unlock()

        // Scale down now (a few ms), so no reference to ReplayKit's buffer outlives this call.
        let started = CACurrentMediaTime()
        let result: Result<FrameSampler.Frame, Error> = autoreleasepool {
            guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else {
                return .failure(FrameSampler.SampleError.format(0))
            }
            let orientation = FrameSampler.orientation(of: sampleBuffer)
            return Result { try sampler.frame(from: pixelBuffer, orientation: orientation) }
        }
        let frame: FrameSampler.Frame
        switch result {
        case .success(let f):
            frame = f
        case .failure(let error):
            lock.withLock { frameBusy = false; frameErrors += 1; lastFrameError = "\(error)" }
            if frameErrors <= 3 { log("frame error: \(error)") }
            return
        }
        frameQueue.async { [weak self] in
            guard let self else { return }
            autoreleasepool { self.process(frame, seq: seq, path: path, started: started) }
            let memory = ProcessStats.memoryMB()
            self.lock.withLock {
                self.frameBusy = false
                self.memoryPeak = max(self.memoryPeak, memory)
            }
        }
    }

    private func process(_ frame: FrameSampler.Frame, seq: Int, path: ScreenPath, started: CFTimeInterval) {
        let signature = FrameSampler.signature(of: frame)
        let same = lock.withLock { () -> Bool in
            frameSize = "\(frame.width)x\(frame.height)"
            if FrameSampler.isSame(signature, lastSignature) {
                framesSame += 1
                return true
            }
            return false
        }
        if same { return }
        if frameUploader.isFull { return }  // the network is slow; the uploader drops old frames anyway

        switch path {
        case .jpeg:
            guard let data = FrameSampler.jpeg(frame, quality: 0.5) else {
                lock.withLock { frameErrors += 1; lastFrameError = "JPEG encode failed" }
                return
            }
            let ms = Int((CACurrentMediaTime() - started) * 1000)
            lock.withLock { lastSignature = signature; lastFrameMs = ms; lastFrameKB = (Double(data.count) / 102.4).rounded() / 10 }
            frameUploader.offer(.init(seq: seq, path: "api/frame", query: [
                URLQueryItem(name: "w", value: String(frame.width)),
                URLQueryItem(name: "h", value: String(frame.height)),
                URLQueryItem(name: "ms", value: String(ms)),
            ], body: data, contentType: "image/jpeg"))
        case .ocrFast, .ocrAccurate:
            let ocrStart = CACurrentMediaTime()
            do {
                let lines = try FrameSampler.recognize(frame, accurate: path == .ocrAccurate)
                let ms = Int((CACurrentMediaTime() - ocrStart) * 1000)
                let total = Int((CACurrentMediaTime() - started) * 1000)
                let body = try JSONEncoder().encode(OCRBody(session: LensConfig.session, seq: seq, level: path.rawValue, ocrMs: ms,
                                                             width: frame.width, height: frame.height, lines: lines))
                lock.withLock { lastSignature = signature; ocrMs = ms; ocrLines = lines.count; lastFrameMs = total; lastFrameKB = (Double(body.count) / 102.4).rounded() / 10 }
                frameUploader.offer(.init(seq: seq, path: "api/screentext", query: [], body: body, contentType: "application/json"))
            } catch {
                lock.withLock { frameErrors += 1; lastFrameError = "OCR: \(error.localizedDescription)" }
            }
        case .off:
            break
        }
    }

    private struct OCRBody: Encodable {
        let session: String
        let seq: Int
        let level: String
        let ocrMs: Int
        let width: Int
        let height: Int
        let lines: [FrameSampler.TextLine]
    }

    // MARK: Chunks

    /// Cuts a chunk off the front of `pending`. Splits at the quietest 20 ms in the last 0.8 s before the
    /// target length, so a word is less often cut in two. No overlap, so no word comes twice.
    private func flushChunk(force: Bool) {
        lock.lock()
        let target = Int(chunkSeconds * Double(rate))
        guard pending.count >= (force ? rate / 5 : target) else { lock.unlock(); return }
        let limit = min(target, pending.count)
        var cut = limit
        if !force {
            let frame = rate / 50
            let searchStart = max(rate, target - rate * 8 / 10)
            var best = Double.infinity
            var start = searchStart
            while start + frame <= limit {
                var energy: Double = 0
                for i in start..<(start + frame) { let v = Double(pending[i]); energy += v * v }
                if energy < best { best = energy; cut = start + frame }
                start += frame
            }
            if best == .infinity { cut = limit }
        }
        let body = Array(pending[0..<cut])
        pending.removeFirst(cut)
        chunkSeq += 1
        let seq = chunkSeq
        var squares: Double = 0
        for v in body { squares += Double(v) * Double(v) }
        let level = (squares / Double(max(1, body.count))).squareRoot() / 32_768
        let silent = level < silenceRMS
        if silent { silentSkipped += 1 }
        lock.unlock()

        if silent { return }
        uploader.enqueue(ChunkUploader.Chunk(seq: seq, mulaw: MuLaw.encode(body), seconds: Double(body.count) / Double(rate)))
    }

    // MARK: Server poll and Live Activity (path A)

    private func startTimers() {
        let t = DispatchSource.makeTimerSource(queue: queue)
        t.schedule(deadline: .now() + 2, repeating: 2)
        t.setEventHandler { [weak self] in self?.sendHeartbeat() }
        t.resume()
        timer = t

        let p = DispatchSource.makeTimerSource(queue: queue)
        p.schedule(deadline: .now() + 0.5, repeating: 1)
        p.setEventHandler { [weak self] in self?.poll() }
        p.resume()
        pollTimer = p
    }

    private func poll() {
        lock.lock()
        if pollInFlight { lock.unlock(); return }
        pollInFlight = true
        let since = lastResultId
        lock.unlock()
        pollSession.dataTask(with: LensAPI.latestURL(since: since, source: "broadcast")) { [weak self] data, _, error in
            guard let self else { return }
            guard let data, error == nil, let response = try? LensAPI.decode(data) else {
                self.lock.withLock { self.pollInFlight = false; self.pollFailures += 1 }
                return
            }
            self.queue.async { self.handle(response) }
        }.resume()
    }

    private func handle(_ response: LatestResponse) {
        let config = response.config
        let visible = LiveActivitySink.visible.count
        lock.lock()
        pollInFlight = false
        polls += 1
        if let last = response.lastItemId { lastResultId = max(lastResultId, last) }
        if let seconds = config?.chunkSeconds, [3, 4, 5, 8].contains(seconds), seconds != chunkSeconds {
            logLines.append("chunk length now \(Int(seconds)) s")
            chunkSeconds = seconds
        }
        let oldScreen = screenPath
        screenPath = config?.screen ?? .jpeg
        if let value = config?.fps, [0.5, 1, 2].contains(value) { fps = value }
        let screenChanged = oldScreen != screenPath
        if screenChanged { lastSignature = [] }
        let oldPath = updatePath
        updatePath = config?.path ?? .auto
        let configSpeech = config?.speechPath.flatMap(SpeechPath.init(rawValue:))
        let video = response.screen?.videoId
        let langs = response.results.filter { $0.speech && $0.source != "screen" }.compactMap { SpeechLanguage.of($0.text) }
        alerts = config?.alerts ?? true
        alertSound = config?.alertSound ?? false
        let visibleChanged = visible != activitiesVisible
        activitiesVisible = visible
        let path = updatePath
        let allowAlerts = alerts
        let showTechniques = techniqueNotes
        let sound = alertSound
        let held = CACurrentMediaTime() < holdUntil
        let serverPush = response.push?.serverOwnsIsland == true
        lock.unlock()

        if oldPath != path { log("update path now \(path.rawValue)") }
        if let configSpeech { setSpeechPath(configSpeech) }
        updateSpeech(video: video, langs: langs)
        if screenChanged { log("screen path now \(screenPath.rawValue), \(fps) fps") }
        if visibleChanged { log("Live Activities visible from the extension: \(visible)") }

        let active: String
        if serverPush {
            // The server pushes every Island change through APNs: the one owner. The extension sends nothing.
            active = "server push"
        } else if held {
            active = "extension (held for Footnote this)"
        } else if path == .auto && !showTechniques {
            // Footnote checks facts: the app is the one owner of the Island (the lit logo, the result and its hold).
            // Two writers with different states made the Island flicker between them.
            active = "app (checks)"
        } else if path == .auto || path == .fromExtension {
            if visible > 0, var state = response.state {
                if !showTechniques, state.mode == .found {
                    // Footnote checks facts now: a technique note does not reach the Island.
                    state.mode = .listening
                    state.flagText = nil
                    state.flagMark = nil
                    state.flagLabel = nil
                    state.flagShort = nil
                    state.flagWhy = nil
                }
                // Notes that may not expand the Island are quiet in the compact one.
                state.quietNotes = allowAlerts ? nil : true
                sink.apply(state, alerts: allowAlerts, sound: sound) { [weak self] in self?.log($0) }
                active = "extension"
            } else {
                active = visible > 0 ? "extension (no state)" : "none (no activity visible)"
            }
        } else {
            active = "off (\(path.rawValue))"
        }
        lock.withLock { pathActive = active }
    }

    // MARK: On-device speech

    /// On the work queue.
    private func setSpeechPath(_ path: SpeechPath) {
        let old = lock.withLock { () -> SpeechPath in
            let old = speechPath
            speechPath = path
            if old != path { speechBlockedUntil = 0 }
            return old
        }
        guard old != path else { return }
        log("speech path now \(path.rawValue)")
        updateSpeech(video: nil, langs: [])
    }

    /// Starts or stops the recognizer for the path, the video and the languages the server heard. On the work queue.
    private func updateSpeech(video: Int?, langs: [String]) {
        let now = CACurrentMediaTime()
        lock.lock()
        let newVideo = video != nil && video != currentVideo
        if newVideo { currentVideo = video; videoLangs = [] }
        videoLangs = Array((videoLangs + langs).suffix(4))
        let path = speechPath
        let running = speechLang
        let starting = speechStarting
        let blocked = now < speechBlockedUntil
        let known = videoLangs.count >= 2 && Set(videoLangs.suffix(2)).count == 1 ? videoLangs.last : nil
        // No text for 12 s while the sound is loud: the recognizer does not work here.
        let silentRecognizer = running != nil && now - speechStartedAt > 12 && now - loudAt < 2 && now - max(lastFinalAt, speechStartedAt) > 12
        lock.unlock()

        switch path {
        case .upload:
            if running != nil { stopSpeech("speech path upload") }
        case .onDevice:
            if silentRecognizer { log("on-device speech gave no text for 12 s of sound") }
            if running == nil, !starting { startSpeech(lang: Self.phoneLanguage()) }
        case .auto:
            if running != nil, newVideo {
                stopSpeech("new video: sound clips until its language is known")
            } else if silentRecognizer {
                stopSpeech("no text for 12 s of sound: back to sound clips for 60 s")
                lock.withLock { speechBlockedUntil = now + 60 }
            } else if running == nil, !starting, !blocked, let known {
                startSpeech(lang: known)
            }
        }
    }

    /// The first of the phone's languages that on-device speech covers, else English.
    private static func phoneLanguage() -> String {
        for id in Locale.preferredLanguages {
            let code = String(id.prefix(2))
            if ["en", "uk", "ru"].contains(code) { return code }
        }
        return "en"
    }

    private func startSpeech(lang: String) {
        guard #available(iOS 26.0, *) else {
            lock.withLock { speechNote = "upload (on-device speech needs iOS 26)" }
            return
        }
        lock.withLock { speechStarting = true }
        Task { [self] in
            let installed = await OnDeviceSpeech.installed(lang)
            self.queue.async {
                guard installed else {
                    self.lock.withLock { self.speechStarting = false; self.speechNote = "upload (no on-device model for \(lang): Debug > Speech)" }
                    self.log("no on-device speech model for \(lang); sound clips go on")
                    return
                }
                // The sound before the switch still goes up as a chunk, so no word is lost.
                self.flushChunk(force: true)
                let engine = OnDeviceSpeech(lang: lang)
                engine.onFinal = { [weak self] final in self?.postText(final.text, lang: final.lang, start: final.start, end: final.end, final: true) }
                engine.onVolatile = { [weak self] text in self?.volatileText(text, lang: lang) }
                engine.onFail = { [weak self] reason in
                    guard let self else { return }
                    self.queue.async {
                        self.stopSpeech("on-device speech failed: \(reason)")
                        self.lock.withLock { self.speechBlockedUntil = CACurrentMediaTime() + 60 }
                    }
                }
                self.lock.withLock {
                    self.speechEngine = engine
                    self.speechLang = lang
                    self.speechStarting = false
                    self.speechStartedAt = CACurrentMediaTime()
                    self.lastFinalAt = 0
                    self.speechNote = "on-device \(lang)"
                }
                engine.start()
                self.lock.withLock { self.speechNote = "on-device \(lang) (\(engine.module))" }
                self.log("on-device speech started: \(lang), \(engine.module); no sound leaves the phone now")
            }
        }
    }

    private func stopSpeech(_ reason: String) {
        let engine = lock.withLock { () -> AnyObject? in
            let e = speechEngine
            speechEngine = nil
            speechLang = nil
            speechNote = "upload"
            return e
        }
        guard let engine else { return }
        if #available(iOS 26.0, *), let speech = engine as? OnDeviceSpeech { speech.stop() }
        log("on-device speech stopped: \(reason)")
    }

    /// A partial text warms the server's detector, at most each 1.5 s.
    private func volatileText(_ text: String, lang: String) {
        let now = CACurrentMediaTime()
        let send = lock.withLock { () -> Bool in
            guard now - lastVolatileAt >= 1.5 else { return false }
            lastVolatileAt = now
            return true
        }
        if send { postText(text, lang: lang, start: nil, end: nil, final: false) }
    }

    private func postText(_ text: String, lang: String, start: Double?, end: Double?, final: Bool) {
        if final { lock.withLock { lastFinalAt = CACurrentMediaTime() } }
        var body: [String: Any] = ["session": LensConfig.session, "source": "speech", "text": text, "lang": lang, "final": final]
        if let start { body["t_start"] = (start * 100).rounded() / 100 }
        if let end { body["t_end"] = (end * 100).rounded() / 100 }
        var request = URLRequest(url: LensConfig.serverURL.appendingPathComponent("api/text"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        let t0 = CACurrentMediaTime()
        textSession.dataTask(with: request) { [weak self] _, response, error in
            guard let self else { return }
            let ok = error == nil && (response as? HTTPURLResponse)?.statusCode == 200
            let ms = Int((CACurrentMediaTime() - t0) * 1000)
            self.lock.withLock {
                if ok { self.textsSent += 1 } else { self.textFailures += 1 }
                if final { self.lastTextMs = ms }
            }
        }.resume()
    }

    // MARK: Heartbeat and log

    private func sendHeartbeat() {
        let upload = uploader.stats
        let frames = frameUploader.stats
        let memory = ProcessStats.memoryMB()
        let kernelPeak = ProcessStats.peakMB()
        lock.lock()
        memoryPeak = max(memoryPeak, memory, kernelPeak)
        let rms = windowSamples > 0 ? (windowSquares / Double(windowSamples)).squareRoot() : 0
        lastRMS = rms
        maxRMS = max(maxRMS, rms)
        windowSquares = 0
        windowSamples = 0
        let body: [String: Any] = [
            "source": "broadcast",
            "session": LensConfig.session,
            "state": state,
            "video": videoCount,
            "audioApp": audioAppCount,
            "audioMic": audioMicCount,
            "audioAppSeconds": (audioAppSeconds * 10).rounded() / 10,
            "rms": (rms * 10_000).rounded() / 10_000,
            "rmsMax": (maxRMS * 10_000).rounded() / 10_000,
            "micRms": (micRMS * 10_000).rounded() / 10_000,
            "chunkSeconds": chunkSeconds,
            "chunksMade": chunkSeq,
            "chunksSent": upload.sent,
            "bytesSent": upload.bytes,
            "postErrors": upload.failed,
            "retries": upload.retries,
            "droppedChunks": upload.dropped,
            "queued": upload.queued,
            "inFlight": upload.busy ? 1 : 0,
            "lastUploadMs": upload.lastMs,
            "silentSkipped": silentSkipped,
            "encoding": "mulaw",
            "updatePath": updatePath.rawValue,
            "updatePathActive": pathActive,
            "activitiesVisible": activitiesVisible,
            "activityUpdatesFromExtension": sink.updates,
            "activityUpdatesVerified": sink.verified,
            "activityAlerts": sink.alerts,
            "activityLastError": sink.lastError,
            "polls": polls,
            "pollFailures": pollFailures,
            "format": format,
            "uptime": Int(Date().timeIntervalSince(startedAt)),
            "memoryMB": memory,
            "memoryPeakMB": memoryPeak,
            "memoryKernelPeakMB": kernelPeak,
            "screenPath": screenPath.rawValue,
            "fps": fps,
            "framesSeen": videoCount,
            "framesTaken": framesTaken,
            "framesSame": framesSame,
            "framesSent": frames.sent,
            "framesDropped": frames.dropped,
            "frameRetries": frames.retries,
            "frameErrors": frameErrors + frames.failed,
            "frameLastError": lastFrameError,
            "frameBytes": frames.bytes,
            "frameSize": frameSize,
            "lastFrameMs": lastFrameMs,
            "lastFrameKB": lastFrameKB,
            "lastFrameUploadMs": frames.lastMs,
            "ocrMs": ocrMs,
            "ocrLines": ocrLines,
            "speechPath": speechPath.rawValue,
            "speechActive": speechNote,
            "textsSent": textsSent,
            "textFailures": textFailures,
            "lastTextMs": lastTextMs,
            "bundle": Bundle.main.bundleIdentifier ?? "",
        ]
        let lines = logLines
        logLines.removeAll()
        lock.unlock()

        ServerPost.json("api/heartbeat", body)
        if !lines.isEmpty {
            ServerPost.json("api/log", ["source": "broadcast", "session": LensConfig.session, "lines": lines])
        }
    }

    private func setState(_ value: String) {
        lock.lock(); state = value; lock.unlock()
    }

    private func log(_ line: String) {
        NSLog("[lens.broadcast] %@", line)
        lock.lock()
        logLines.append(line)
        if logLines.count > 50 { logLines.removeFirst(logLines.count - 50) }
        lock.unlock()
    }
}
