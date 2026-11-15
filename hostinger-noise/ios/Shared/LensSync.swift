import ActivityKit
import Foundation

/// Who updates the Live Activity. Chosen in the app, sent to the server, read back by the extension.
enum UpdatePath: String, CaseIterable, Identifiable, Codable {
    /// The extension and the app both update with the same state; only one of them sends alerts.
    case auto
    /// Only the broadcast extension updates (path A).
    case fromExtension = "extension"
    /// Only the app updates, kept alive with silent audio (path B).
    case app
    /// Only the server updates, with APNs Live Activity pushes (path C).
    case push

    var id: String { rawValue }

    var title: String {
        switch self {
        case .auto: return "Auto (extension and app)"
        case .fromExtension: return "Extension only (A)"
        case .app: return "App with silent audio (B)"
        case .push: return "APNs push (C)"
        }
    }
}

/// How the broadcast extension reads the screen. Chosen in the app, read back by the extension.
enum ScreenPath: String, CaseIterable, Identifiable, Codable {
    /// S1: a small grey JPEG goes to the server, which reads it with a vision model.
    case jpeg
    /// S2: Vision OCR on the phone (fast level); only the text and its boxes leave the phone.
    case ocrFast = "ocr-fast"
    /// S2 with the accurate level: better text, more memory and time.
    case ocrAccurate = "ocr-accurate"
    /// Audio only.
    case off

    var id: String { rawValue }

    var title: String {
        switch self {
        case .jpeg: return "Small JPEG to server (S1)"
        case .ocrFast: return "On-device OCR, fast (S2)"
        case .ocrAccurate: return "On-device OCR, accurate (S2)"
        case .off: return "Off (audio only)"
        }
    }
}

/// Settings the app keeps on the server for the session.
struct LensRemoteConfig: Decodable, Equatable {
    var updatePath: String?
    var chunkSeconds: Double?
    var alerts: Bool?
    var alertSound: Bool?
    var running: Bool?
    var startId: Int?
    var screenPath: String?
    var fps: Double?
    /// The speech path, when the server keeps it (the app also sends it to the extension as a Darwin notification).
    var speechPath: String?

    var path: UpdatePath { UpdatePath(rawValue: updatePath ?? "") ?? .auto }
    var screen: ScreenPath { ScreenPath(rawValue: screenPath ?? "") ?? .jpeg }
}

struct TranscriptResult: Decodable, Equatable {
    let id: Int
    let text: String
    let speech: Bool
    let technique: String?
    let label: String?
    let latency: Double
    var short: String? = nil
    var why: String? = nil
    /// A longer reason for the note detail in the app.
    var explain: String? = nil
    var mark: String? = nil
    /// "audio" (speech) or "screen" (a note on the text on the screen).
    var source: String? = nil
    /// The video the line came from (the server counts videos per session) and its creator.
    var videoId: Int? = nil
    var handle: String? = nil
    /// What was on the screen when a note was made: burned-in text and the caption. Only on notes.
    var onScreen: String? = nil
    var description: String? = nil
    /// The detector is not sure: the brand's dotted line, no marker, no technique name.
    var unsure: Bool? = nil
    var confidence: Double? = nil
    /// The app the line came from ("tiktok", "youtube", "news") and its author, from the screen read.
    var app: String? = nil
    var author: String? = nil
    /// The answer to "Footnote this" (POST /api/now): judged at once, with a lower bar.
    var forced: Bool? = nil
    /// A fact-check of this sentence, when the server puts it on the sentence itself.
    var check: ClaimCheck? = nil
}

/// A fact-check of one sentence that comes back later: what sources say about a claim. It never says true or false:
/// it is a note on what the sources say, like the other notes.
struct ClaimCheck: Codable, Hashable {
    enum Status: String, Codable, Hashable {
        /// The server looks for sources now.
        case pending
        /// Sources say something about the claim: `line` and `sources`.
        case found
        /// No source says anything about it: `line` says so.
        case nothing
    }

    struct Source: Codable, Hashable {
        var title: String
        var url: String?
        /// As the server gives it, such as "2026-11-12".
        var date: String?

        init(title: String, url: String? = nil, date: String? = nil) {
            self.title = title
            self.url = url
            self.date = date
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: Key.self)
            url = try? c.decodeIfPresent(String.self, forKey: .url)
            date = (try? c.decodeIfPresent(String.self, forKey: .date)) ?? (try? c.decodeIfPresent(String.self, forKey: .published))
            let named = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? (try? c.decodeIfPresent(String.self, forKey: .name))
            title = named ?? url.flatMap { URL(string: $0)?.host() }.map { $0.replacingOccurrences(of: "www.", with: "") } ?? "Source"
        }

        func encode(to encoder: Encoder) throws {
            var c = encoder.container(keyedBy: Key.self)
            try c.encode(title, forKey: .title)
            try c.encodeIfPresent(url, forKey: .url)
            try c.encodeIfPresent(date, forKey: .date)
        }

        private enum Key: String, CodingKey { case title, name, url, date, published }

        var link: URL? { url.flatMap(URL.init(string:)).flatMap { ["http", "https"].contains($0.scheme ?? "") ? $0 : nil } }

        /// "12 Nov 2026" for an ISO date, else the date as given.
        var dateLine: String? {
            guard let date, !date.isEmpty else { return nil }
            let iso = DateFormatter()
            iso.locale = Locale(identifier: "en_US_POSIX")
            iso.dateFormat = "yyyy-MM-dd"
            guard let parsed = iso.date(from: String(date.prefix(10))) else { return date }
            return parsed.formatted(.dateTime.day().month(.abbreviated).year())
        }

        /// "Reuters, 12 Nov 2026".
        var line: String { [title, dateLine].compactMap { $0 }.joined(separator: ", ") }
    }

    /// How the sources stand to the claim. Never shown as true or false: the words are about the sources.
    enum Stance: String, Codable, Hashable {
        /// The sources contradict the claim: the main moment of the product, shown on the Island.
        case differ
        case support
        case mixed
        case none
    }

    var status: Status
    /// One line, at most 90 characters, on what the sources say.
    var line: String?
    /// Up to 3 sources.
    var sources: [Source] = []
    /// Nil when the server sends no stance.
    var stance: Stance? = nil
    /// The server wants the Island and the Lock Screen to show this check (it sets it for "differ" and for
    /// "Footnote this"). Without it, a check stays a quiet footnote on the page.
    var notify: Bool? = nil

    var isDone: Bool { status != .pending }
    var notifies: Bool { notify == true && isDone }

    init(status: Status, line: String? = nil, sources: [Source] = [], stance: Stance? = nil, notify: Bool? = nil) {
        self.status = status
        self.line = line
        self.sources = sources
        self.stance = stance
        self.notify = notify
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        line = try? c.decodeIfPresent(String.self, forKey: .line)
        sources = Array(((try? c.decodeIfPresent([Source].self, forKey: .sources)) ?? []).prefix(3))
        let raw = ((try? c.decodeIfPresent(String.self, forKey: .status)) ?? "").lowercased()
        status = Status(rawValue: raw) ?? (line != nil || !sources.isEmpty ? .found : .pending)
        stance = ((try? c.decodeIfPresent(String.self, forKey: .stance)) ?? nil).flatMap { Stance(rawValue: $0.lowercased()) }
        notify = (try? c.decodeIfPresent(Bool.self, forKey: .notify)) ?? nil
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: Key.self)
        try c.encode(status, forKey: .status)
        try c.encodeIfPresent(line, forKey: .line)
        try c.encode(sources, forKey: .sources)
        try c.encodeIfPresent(stance, forKey: .stance)
        try c.encodeIfPresent(notify, forKey: .notify)
    }

    private enum Key: String, CodingKey { case status, line, sources, stance, notify }
}

/// A server item of kind "check": the check of an earlier sentence (`of`, the id of that sentence's result).
struct CheckResult: Decodable, Equatable {
    let id: Int?
    let of: Int?
    /// The claim, the words the check is about (the sentence or a part of it).
    let claim: String?
    let check: ClaimCheck
    /// The answer to "Footnote this": shown on the Island whatever the stance.
    var forced: Bool? = nil
    /// The creator of the video the claim is from, when the server sends it.
    var handle: String? = nil
    var videoId: Int? = nil

    init(id: Int?, of: Int?, claim: String?, check: ClaimCheck, forced: Bool? = nil, handle: String? = nil, videoId: Int? = nil) {
        self.id = id
        self.of = of
        self.claim = claim
        self.check = check
        self.forced = forced
        self.handle = handle
        self.videoId = videoId
    }

    /// The key of one check result, so the same result reaches the Island only once.
    var key: String { "\(of ?? id ?? -1)-\(check.status.rawValue)" }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        id = try? c.decodeIfPresent(Int.self, forKey: .id)
        of = [Key.of, .checkOf, .sentenceId, .resultId, .sentence].lazy.compactMap { try? c.decodeIfPresent(Int.self, forKey: $0) }.first
        claim = (try? c.decodeIfPresent(String.self, forKey: .claim)) ?? (try? c.decodeIfPresent(String.self, forKey: .text))
        // The check fields sit on the item itself, or in a nested "check" object.
        var check = try (try? c.decodeIfPresent(ClaimCheck.self, forKey: .check)) ?? ClaimCheck(from: decoder)
        // stance and notify may sit on the item while the rest is nested.
        if check.stance == nil, let raw = (try? c.decodeIfPresent(String.self, forKey: .stance)) ?? nil { check.stance = ClaimCheck.Stance(rawValue: raw.lowercased()) }
        if check.notify == nil { check.notify = (try? c.decodeIfPresent(Bool.self, forKey: .notify)) ?? nil }
        self.check = check
        forced = (try? c.decodeIfPresent(Bool.self, forKey: .forced)) ?? nil
        handle = (try? c.decodeIfPresent(String.self, forKey: .handle)) ?? nil
        videoId = (try? c.decodeIfPresent(Int.self, forKey: .videoId)) ?? nil
    }

    private enum Key: String, CodingKey { case id, of, checkOf, sentenceId, resultId, sentence, claim, text, check, stance, notify, forced, handle, videoId }
}

struct BroadcastBeat: Decodable, Equatable {
    let state: String?
    let audioApp: Int?
    let video: Int?
    let rms: Double?
    let chunksSent: Int?
    let ts: Double?
    let updatePathActive: String?
    let activitiesVisible: Int?
    let activityUpdatesFromExtension: Int?
    let memoryMB: Double?
    var speechActive: String? = nil
    let memoryPeakMB: Double?
    let framesSent: Int?
    let screenPath: String?
}

/// What the server read from the screen for this session.
struct NowPlaying: Decodable, Equatable {
    let videoId: Int?
    let app: String?
    let handle: String?
    let description: String?
    let onScreenText: String?
    let likes: String?
    let paused: Bool?
    let reads: Int?
    let age: Double?
}

/// The server's APNs Live Activity pushes for this session (`push` in /api/latest). An old server sends none.
struct PushStatus: Decodable, Equatable {
    /// The last APNs push to this session's token got HTTP 200, and none failed since: the server owns the Island.
    var ok: Bool?
    /// The server has a push token for this session.
    var tokenSet: Bool?

    /// One owner of the Island: while this is true, neither the app nor the extension sends an update of its own.
    var serverOwnsIsland: Bool { ok == true }
}

struct LatestResponse: Decodable {
    let results: [TranscriptResult]
    /// Items of kind "check" from `results` (and from a `checks` list, if the server sends one).
    let checks: [CheckResult]
    /// The highest item id in `results`, checks included, so polling moves past a check.
    let lastItemId: Int?
    /// The id of the server's newest item of any kind (a check too), to see a server restart.
    let latestId: Int?
    let screen: NowPlaying?
    let latest: TranscriptResult?
    let count: Int
    let techniques: Int
    let broadcast: BroadcastBeat?
    let server_time: Double
    let state: LensActivityAttributes.ContentState?
    let config: LensRemoteConfig?
    /// Nil from an old server.
    let push: PushStatus?

    /// One entry of `results`. An entry the app cannot read is skipped, so a new item kind never breaks the poll.
    private struct Item: Decodable {
        let id: Int?
        let result: TranscriptResult?
        let check: CheckResult?

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: Key.self)
            id = try? c.decodeIfPresent(Int.self, forKey: .id)
            if (try? c.decodeIfPresent(String.self, forKey: .kind)) == "check" {
                result = nil
                check = try? CheckResult(from: decoder)
            } else {
                result = try? TranscriptResult(from: decoder)
                check = nil
            }
        }

        private enum Key: String, CodingKey { case id, kind }
    }

    private enum Key: String, CodingKey { case results, checks, screen, latest, count, techniques, broadcast, server_time, state, config, push }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        let items = try c.decode([Item].self, forKey: .results)
        results = items.compactMap(\.result)
        checks = items.compactMap(\.check) + ((try? c.decodeIfPresent([CheckResult].self, forKey: .checks)) ?? [])
        lastItemId = items.compactMap(\.id).max()
        screen = try? c.decodeIfPresent(NowPlaying.self, forKey: .screen)
        latest = try? c.decodeIfPresent(TranscriptResult.self, forKey: .latest)
        latestId = (try? c.decodeIfPresent(Item.self, forKey: .latest))?.id
        count = (try? c.decodeIfPresent(Int.self, forKey: .count)) ?? 0
        techniques = (try? c.decodeIfPresent(Int.self, forKey: .techniques)) ?? 0
        broadcast = try? c.decodeIfPresent(BroadcastBeat.self, forKey: .broadcast)
        server_time = try c.decode(Double.self, forKey: .server_time)
        state = try? c.decodeIfPresent(LensActivityAttributes.ContentState.self, forKey: .state)
        config = try? c.decodeIfPresent(LensRemoteConfig.self, forKey: .config)
        push = try? c.decodeIfPresent(PushStatus.self, forKey: .push)
    }
}

enum LensAPI {
    static func latestURL(since: Int, source: String) -> URL {
        var components = URLComponents(url: LensConfig.serverURL.appendingPathComponent("api/latest"), resolvingAgainstBaseURL: false)!
        components.queryItems = [
            URLQueryItem(name: "session", value: LensConfig.session),
            URLQueryItem(name: "since", value: String(since)),
            URLQueryItem(name: "source", value: source),
        ]
        return components.url!
    }

    static func decode(_ data: Data) throws -> LatestResponse {
        try JSONDecoder().decode(LatestResponse.self, from: data)
    }
}

/// Sends a content state to the running Live Activity. Used by the app and by the broadcast extension,
/// so both behave the same. Skips equal states and sends an alert when the count goes up.
final class LiveActivitySink: @unchecked Sendable {
    typealias State = LensActivityAttributes.ContentState

    private let lock = NSLock()
    private let source: String
    private var last: State?
    private var lastActivityID: String?
    private var _updates = 0
    private var _verified = 0
    private var _alerts = 0
    private var _lastError = ""

    init(source: String) {
        self.source = source
    }

    var updates: Int { lock.withLock { _updates } }
    /// Updates where the activity reported the new state back afterwards.
    var verified: Int { lock.withLock { _verified } }
    var alerts: Int { lock.withLock { _alerts } }
    var lastError: String { lock.withLock { _lastError } }
    /// Whether the activity read back the last state sent (nil before the first read).
    var lastEcho: Bool? { lock.withLock { _lastEcho } }
    private var _lastEcho: Bool?

    /// Activities of this app that this process can see.
    static var visible: [Activity<LensActivityAttributes>] {
        Activity<LensActivityAttributes>.activities.filter { $0.activityState == .active || $0.activityState == .stale }
    }

    /// Forget the last state, so the next apply sends again (for example after a new activity started).
    func reset() {
        lock.withLock { last = nil; lastActivityID = nil }
    }

    /// Returns false when nothing was sent (no activity, or the same state).
    @discardableResult
    func apply(_ state: State, alerts allowAlerts: Bool, sound: Bool, forceAlert: Bool = false, log: @escaping (String) -> Void) -> Bool {
        guard let activity = Self.visible.first else {
            lock.withLock { _lastError = "no active Live Activity visible to \(source)" }
            return false
        }
        lock.lock()
        if lastActivityID != activity.id {
            lastActivityID = activity.id
            last = nil
        }
        if last == state {
            lock.unlock()
            return false
        }
        let previous = last
        last = state
        lock.unlock()

        // "Footnote this" always expands the Island: the press must show at once, and so must its answer.
        let alert = forceAlert || (allowAlerts && previous != nil && state.mode == .found && state.count > (previous?.count ?? 0))
        let config: AlertConfiguration? = alert
            ? AlertConfiguration(
                title: LocalizedStringResource(stringLiteral: state.mode == .found ? "\(state.flagLabel ?? "Note")" : "Footnote"),
                body: LocalizedStringResource(stringLiteral: state.flagText ?? (state.line.isEmpty ? (state.flagLabel ?? "") : state.line)),
                sound: sound ? .default : .named("silence.caf"))
            : nil
        Task {
            await activity.update(ActivityContent(state: state, staleDate: nil), alertConfiguration: config)
            // Read the state back a moment later (right after the update it can still show the old one). A Date can
            // come back with a different precision, so updatedAt does not count.
            try? await Task.sleep(for: .milliseconds(500))
            var back = activity.content.state
            back.updatedAt = state.updatedAt
            let echoed = back == state
            self.lock.withLock {
                self._updates += 1
                if echoed { self._verified += 1 }
                self._lastEcho = echoed
                if alert { self._alerts += 1 }
                self._lastError = echoed ? "" : "update sent but the activity reads back \(back.mode.rawValue) (\(activity.activityState))"
            }
            let turn = state.turn.map { ", turn \($0)" } ?? ""
            log("Live Activity update from \(self.source): \(state.mode.rawValue)\(turn)\(alert ? ", alert" : ""), reads back \(back.mode.rawValue)\(echoed ? "" : " (not the same state)")")
        }
        return true
    }
}
