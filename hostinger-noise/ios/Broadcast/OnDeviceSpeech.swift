import AVFoundation
import Foundation
import NaturalLanguage
import Speech

/// The language of a transcript, for the auto speech path: "en", "uk", "ru" or nil when unsure.
enum SpeechLanguage {
    static func of(_ text: String) -> String? {
        let recognizer = NLLanguageRecognizer()
        recognizer.languageConstraints = [.english, .ukrainian, .russian]
        recognizer.processString(text)
        guard let (language, p) = recognizer.languageHypotheses(withMaximum: 1).first, p >= 0.8 else { return nil }
        return language.rawValue
    }
}

/// On-device speech to text in the broadcast extension, with SpeechAnalyzer (iOS 26). It gets the same 16 kHz mono
/// samples as the chunk path. English uses SpeechTranscriber with fast results: a volatile text about each second and
/// a final text per sentence 0.6 to 1.0 s after it ends (measured on a Mac, 14 November 2026). SpeechTranscriber has
/// no Ukrainian or Russian, so those use DictationTranscriber (word by word, 0.0 to 0.2 s behind the sound). The model
/// runs in a system process: on the Mac the process footprint stayed at 7 MB, so it fits the extension's 50 MB.
/// The extension never downloads a model: the app does (Debug > Speech), and `installed` says whether the phone has it.
@available(iOS 26.0, *)
final class OnDeviceSpeech: @unchecked Sendable {
    struct Final {
        let text: String
        let lang: String
        let start: Double
        let end: Double
    }

    let lang: String
    var onFinal: ((Final) -> Void)?
    var onVolatile: ((String) -> Void)?
    var onFail: ((String) -> Void)?
    private(set) var module = ""

    private let lock = NSLock()
    private let sourceFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16_000, channels: 1, interleaved: true)!
    private var continuation: AsyncStream<AnalyzerInput>.Continuation?
    private var analyzer: SpeechAnalyzer?
    private var converter: AVAudioConverter?
    private var targetFormat: AVAudioFormat?
    private var backlog: [[Int16]] = []
    private var backlogSamples = 0
    private var tasks: [Task<Void, Never>] = []
    private var stopped = false

    init(lang: String) {
        self.lang = lang
    }

    static func locale(for lang: String) -> Locale {
        switch lang {
        case "uk": return Locale(identifier: "uk_UA")
        case "ru": return Locale(identifier: "ru_RU")
        default: return Locale(identifier: "en_US")
        }
    }

    /// True when the phone has the model for this language.
    static func installed(_ lang: String) async -> Bool {
        let id = locale(for: lang).identifier(.bcp47)
        let locales = lang == "en" ? await SpeechTranscriber.installedLocales : await DictationTranscriber.installedLocales
        return locales.contains { $0.identifier(.bcp47) == id }
    }

    func start() {
        let task = Task { [weak self] in
            guard let self else { return }
            do {
                try await self.run()
            } catch {
                self.onFail?("start: \(error.localizedDescription)")
            }
        }
        lock.withLock { tasks.append(task) }
    }

    private func run() async throws {
        let locale = Self.locale(for: lang)
        let speechModule: any SpeechModule
        if lang == "en" {
            let transcriber = SpeechTranscriber(locale: locale, transcriptionOptions: [], reportingOptions: [.volatileResults, .fastResults],
                                                attributeOptions: [.audioTimeRange])
            speechModule = transcriber
            module = "SpeechTranscriber"
            listen { for try await r in transcriber.results { self.result(String(r.text.characters), r.range, final: r.isFinal) } }
        } else {
            let transcriber = DictationTranscriber(locale: locale, contentHints: [], transcriptionOptions: [.punctuation],
                                                   reportingOptions: [.volatileResults], attributeOptions: [.audioTimeRange])
            speechModule = transcriber
            module = "DictationTranscriber"
            listen { for try await r in transcriber.results { self.result(String(r.text.characters), r.range, final: r.isFinal) } }
        }
        guard let format = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [speechModule]) else {
            throw NSError(domain: "OnDeviceSpeech", code: 1, userInfo: [NSLocalizedDescriptionKey: "no audio format for \(locale.identifier)"])
        }
        let (stream, continuation) = AsyncStream<AnalyzerInput>.makeStream(bufferingPolicy: .bufferingNewest(200))
        let analyzer = SpeechAnalyzer(modules: [speechModule])
        try await analyzer.start(inputSequence: stream)
        let pending: [[Int16]] = lock.withLock {
            self.analyzer = analyzer
            self.continuation = continuation
            self.targetFormat = format
            if format != sourceFormat { converter = AVAudioConverter(from: sourceFormat, to: format) }
            let out = backlog
            backlog = []
            backlogSamples = 0
            return out
        }
        for samples in pending { feed(samples) }
    }

    private func listen(_ body: @escaping @Sendable () async throws -> Void) {
        let task = Task { [weak self] in
            do {
                try await body()
            } catch {
                if self?.lock.withLock({ self?.stopped }) != true { self?.onFail?("results: \(error.localizedDescription)") }
            }
        }
        lock.withLock { tasks.append(task) }
    }

    private func result(_ text: String, _ range: CMTimeRange, final: Bool) {
        let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if module == "DictationTranscriber" {
            dictation(clean, range, final: final)
            return
        }
        guard !clean.isEmpty else { return }
        if final {
            onFinal?(Final(text: clean, lang: lang, start: range.start.seconds, end: range.end.seconds))
        } else {
            onVolatile?(clean)
        }
    }

    // DictationTranscriber gives one growing volatile text and a final only at a long pause (none in 30 s of a
    // TikTok video, also with punctuation). So the stable front of the volatile text becomes a final: all but the
    // last 3 words (they can still change), each 2.5 s or at 14 new words.
    private var sentWords = 0
    private var lastCut: CFTimeInterval = 0
    private var lastCutEnd: Double = 0

    private func dictation(_ text: String, _ range: CMTimeRange, final: Bool) {
        let words = text.split(whereSeparator: \.isWhitespace).map(String.init)
        let now = CACurrentMediaTime()
        if lastCut == 0 { lastCut = now }
        if final {
            let rest = words.dropFirst(min(sentWords, words.count))
            if !rest.isEmpty {
                onFinal?(Final(text: rest.joined(separator: " "), lang: lang, start: lastCutEnd, end: range.end.seconds))
            }
            sentWords = 0
            lastCut = now
            lastCutEnd = range.end.seconds
            return
        }
        if !text.isEmpty { onVolatile?(text) }
        let stable = words.count - 3
        let fresh = stable - sentWords
        guard fresh >= 14 || (fresh >= 5 && now - lastCut >= 2.5) else { return }
        let end = range.end.seconds
        onFinal?(Final(text: words[sentWords..<stable].joined(separator: " "), lang: lang, start: lastCutEnd, end: end))
        sentWords = stable
        lastCut = now
        lastCutEnd = end
    }

    /// 16 kHz mono Int16 samples, in order. Before the analyzer runs, up to 10 s wait in a backlog.
    func feed(_ samples: [Int16]) {
        guard !samples.isEmpty else { return }
        lock.lock()
        guard let continuation, let targetFormat else {
            if backlogSamples < 160_000 {
                backlog.append(samples)
                backlogSamples += samples.count
            }
            lock.unlock()
            return
        }
        let converter = self.converter
        lock.unlock()
        guard let buffer = AVAudioPCMBuffer(pcmFormat: sourceFormat, frameCapacity: AVAudioFrameCount(samples.count)) else { return }
        buffer.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { src in
            buffer.int16ChannelData?[0].update(from: src.baseAddress!, count: samples.count)
        }
        guard let converter else {
            continuation.yield(AnalyzerInput(buffer: buffer))
            return
        }
        let capacity = AVAudioFrameCount(Double(samples.count) * targetFormat.sampleRate / 16_000) + 64
        guard let out = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: capacity) else { return }
        var given = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if given {
                status.pointee = .noDataNow
                return nil
            }
            given = true
            status.pointee = .haveData
            return buffer
        }
        if error == nil, out.frameLength > 0 { continuation.yield(AnalyzerInput(buffer: out)) }
    }

    /// Finishes the open sentence, then ends the analyzer.
    func stop() {
        let (continuation, analyzer, tasks) = lock.withLock { () -> (AsyncStream<AnalyzerInput>.Continuation?, SpeechAnalyzer?, [Task<Void, Never>]) in
            stopped = true
            return (self.continuation, self.analyzer, self.tasks)
        }
        continuation?.finish()
        Task {
            try? await analyzer?.finalizeAndFinishThroughEndOfInput()
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            tasks.forEach { $0.cancel() }
        }
    }
}
