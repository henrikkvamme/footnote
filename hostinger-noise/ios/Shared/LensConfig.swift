import Foundation

/// Values that the app and the broadcast extension share without an App Group.
enum LensConfig {
    /// The test server on the Hostinger VPS (hostinger-noise/ios/server).
    static let serverURL = URL(string: "https://lens.srv2159196.hstgr.cloud")!
    /// One fixed session, so the app and the extension find each other's data on the server.
    /// `lensSession` in the app's defaults overrides it, so a simulator test does not mix with the phone.
    /// The launch argument `-lensSession NAME` sets it for one run (tools/demo.sh), and Debug sets it for good.
    static let session = UserDefaults.standard.string(forKey: "lensSession").flatMap { $0.isEmpty ? nil : $0 } ?? "lens-demo"
    /// `-lensFollow YES` at launch: skip the first run and start listening at once, without the broadcast sheet, so
    /// the app follows a session that `tools/replay.py` feeds (no phone, no taps).
    static let follow = UserDefaults.standard.bool(forKey: "lensFollow")
    static let broadcastBundleID = "no.henrikkvamme.lens.broadcast"
}

/// Darwin notifications from the broadcast extension to the app. They need no App Group and arrive at once,
/// so the app knows the broadcast runs before the first heartbeat reaches the server.
enum BroadcastSignal: String {
    case started = "no.henrikkvamme.lens.broadcast.started"
    case finished = "no.henrikkvamme.lens.broadcast.finished"
    /// The app shows a "Footnote this" answer: the extension leaves the Live Activity alone for a few seconds.
    case holdIsland = "no.henrikkvamme.lens.island.hold"

    /// The app's speech path, sent when the broadcast starts and when it changes. A Darwin notification carries no
    /// value, so each path has its own name.
    case speechUpload = "no.henrikkvamme.lens.speech.upload"
    case speechOnDevice = "no.henrikkvamme.lens.speech.onDevice"
    case speechAuto = "no.henrikkvamme.lens.speech.auto"

    /// Debug "Technique notes": on, the Island shows technique notes as before; off (the product now), only checks.
    case techniqueOn = "no.henrikkvamme.lens.technique.on"
    case techniqueOff = "no.henrikkvamme.lens.technique.off"

    /// An intent that iOS ran in the widget extension instead of the app: the app does the work when it runs.
    case footnoteNowRequest = "no.henrikkvamme.lens.intent.now"
    case newTakeRequest = "no.henrikkvamme.lens.intent.newTake"

    func post() {
        CFNotificationCenterPostNotification(CFNotificationCenterGetDarwinNotifyCenter(), CFNotificationName(rawValue as CFString), nil, nil, true)
    }
}

/// Where speech becomes text.
/// - upload: 4 s sound clips go to the server, which transcribes them (all languages).
/// - onDevice: the phone transcribes (SpeechAnalyzer, iOS 26) and sends only the text to POST /api/text.
///   No sound leaves the phone. English uses SpeechTranscriber; Ukrainian and Russian use DictationTranscriber.
/// - auto: upload until the server's transcripts of a video show a language the phone can transcribe, then on-device
///   for the rest of that video. Sound clips leave the phone at the start of each video.
enum SpeechPath: String, CaseIterable, Identifiable {
    case upload, onDevice, auto

    var id: String { rawValue }

    var title: String {
        switch self {
        case .upload: return "Sound clips to the server"
        case .onDevice: return "On the phone (text only)"
        case .auto: return "Auto (on the phone when it can)"
        }
    }

    var signal: BroadcastSignal {
        switch self {
        case .upload: return .speechUpload
        case .onDevice: return .speechOnDevice
        case .auto: return .speechAuto
        }
    }

    init?(signal raw: String) {
        guard let path = SpeechPath.allCases.first(where: { $0.signal.rawValue == raw }) else { return nil }
        self = path
    }
}

enum ProcessStats {
    private static func vmInfo() -> task_vm_info_data_t? {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<integer_t>.size)
        let result = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
            }
        }
        return result == KERN_SUCCESS ? info : nil
    }

    /// Physical memory footprint in MB, the number that the extension memory limit (about 50 MB) counts.
    static func memoryMB() -> Double {
        guard let info = vmInfo() else { return -1 }
        return (Double(info.phys_footprint) / 1_048_576 * 10).rounded() / 10
    }

    /// The highest footprint of this process so far, as the kernel counts it (also between our samples).
    static func peakMB() -> Double {
        guard let info = vmInfo() else { return -1 }
        return (Double(info.ledger_phys_footprint_peak) / 1_048_576 * 10).rounded() / 10
    }
}

/// Small JSON poster for the server. Fire and forget.
enum ServerPost {
    static let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 15
        config.waitsForConnectivity = false
        return URLSession(configuration: config)
    }()

    static func json(_ path: String, _ body: [String: Any], completion: ((Bool) -> Void)? = nil) {
        guard let data = try? JSONSerialization.data(withJSONObject: body) else { completion?(false); return }
        var request = URLRequest(url: LensConfig.serverURL.appendingPathComponent(path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        session.uploadTask(with: request, from: data) { _, response, error in
            completion?(error == nil && (response as? HTTPURLResponse)?.statusCode == 200)
        }.resume()
    }
}
