import ActivityKit
import AudioToolbox
import AVFoundation
import Observation
import ReplayKit
import SwiftUI
import UIKit

/// Ties the parts together. "Start listening" starts the Live Activity, keeps the app alive with silent audio
/// and opens the broadcast picker. The server computes the Live Activity state; whoever the update path names
/// (the extension, this app, or the server with APNs) sends it to the Island. A heartbeat goes to the server each 2 s.
@MainActor
@Observable
final class AppModel {
    /// One model for the scene and for the intents (iOS may launch the app in the background for an intent).
    static let shared = AppModel()

    // Settings, kept across launches. The ones the extension or the server need also go to /api/config.
    var updatePath: UpdatePath { didSet { save(updatePath.rawValue, "updatePath"); sendConfig() } }
    var chunkSeconds: Double { didSet { save(chunkSeconds, "chunkSeconds"); sendConfig() } }
    var activityAlerts: Bool { didSet { save(activityAlerts, "activityAlerts"); sendConfig() } }
    var alertSound: Bool { didSet { save(alertSound, "alertSound"); sendConfig() } }
    /// Debug: the old technique notes (off by default). Footnote checks facts: off, the page, the Island and the Lock
    /// Screen show only checks, and "Footnote this" delivers the held check. On: technique notes as before.
    var techniqueNotes: Bool {
        didSet { save(techniqueNotes, "techniqueNotes"); sendConfig(); (techniqueNotes ? BroadcastSignal.techniqueOn : .techniqueOff).post() }
    }
    var checksFirst: Bool { !techniqueNotes }
    /// Whether a technique note may expand the Island.
    var techniqueAlerts: Bool { activityAlerts && techniqueNotes }
    var keepAlive: Bool { didSet { save(keepAlive, "keepAlive"); applyKeepAlive() } }
    var screenPath: ScreenPath { didSet { save(screenPath.rawValue, "screenPath"); sendConfig() } }
    var fps: Double { didSet { save(fps, "fps"); sendConfig() } }
    /// Debug: read TikTok only (the old product). Off: any app that shows public media; private screens pause.
    var tiktokOnly: Bool { didSet { save(tiktokOnly, "tiktokOnly"); sendConfig() } }
    /// Where speech becomes text (SpeechPath). The extension learns it from a Darwin notification.
    var speechPath: SpeechPath { didSet { save(speechPath.rawValue, "speechPath"); sendConfig(); speechPath.signal.post() } }
    /// Debug > Speech: the state of the on-device speech models.
    var speechModels = ""
    // Debug: the old PiP caption card.
    var pipEnabled: Bool { didSet { save(pipEnabled, "pipEnabled"); pipEnabledChanged() } }
    var audioVariant: AudioVariant { didSet { save(audioVariant.rawValue, "audioVariant"); audio.apply(audioVariant) } }
    var mixAfterPiPStart: Bool { didSet { save(mixAfterPiPStart, "mixAfterPiPStart") } }
    var deactivateAfterPiPStart: Bool { didSet { save(deactivateAfterPiPStart, "deactivateAfterPiPStart") } }
    var autoPiPFromInline: Bool { didSet { save(autoPiPFromInline, "autoPiPFromInline"); pip.setAutoFromInline(pipEnabled && autoPiPFromInline) } }

    // Live state for the UI.
    private(set) var listening: Bool { didSet { save(listening, "listening") } }
    private(set) var state = LensActivityAttributes.ContentState.idle()
    private(set) var pathActive = "none"
    private(set) var pipPossible = false
    private(set) var pipActive = false
    private(set) var activityState = "none"
    private(set) var lastResultId = 0
    private(set) var broadcast: BroadcastBeat?
    private(set) var broadcastLive = false
    private(set) var serverOK = false
    private(set) var pushReady = false
    private(set) var nowPlaying: NowPlaying?
    private(set) var benchmark: [String] = []
    private(set) var benchmarkRunning = false
    /// This run as a page, kept on the phone (SessionStore). It stays on screen after "Stop".
    private(set) var current: StoredSession?
    /// The sample session from Debug, shown instead of `current` until cleared.
    private(set) var demo: StoredSession?
    /// Past sessions, newest first.
    private(set) var sessions: [StoredSession] = []
    /// The newest line that arrived while the app polls: the page fades it in.
    private(set) var freshItemId: Int?
    private(set) var consecutiveFailures = 0
    /// The broadcast ran in this run and then stopped (iOS or the person ended it).
    private(set) var broadcastEnded = false
    var onboarded: Bool { didSet { save(onboarded, "onboarded") } }
    /// A broadcast has run once on this phone: the start guide before the iOS sheet is no longer needed.
    private(set) var broadcastConfirmed: Bool { didSet { save(broadcastConfirmed, "broadcastConfirmed") } }
    /// The answer to the last "Footnote this", shown in the Island until `nowUntil` instead of the server's state.
    private(set) var nowState: LensActivityAttributes.ContentState?
    private(set) var nowUntil: Date?
    private(set) var nowPresses = 0
    /// Claims under check (the claim's result id and when it came): the logo stays lit while one runs, at most 60 s.
    private var pendingChecks: [Int: Date] = [:]
    /// The logo stays lit at least until then (two turn steps after "Footnote this" with nothing to deliver).
    private var litUntil = Date.distantPast
    /// The claims "Footnote this" waits for: their result goes to the Island whatever the server says.
    private var nowWanted: Set<Int> = []
    /// The demo replay's check, held until "Footnote this" (or until the user would have scrolled on).
    private var demoHeld: CheckResult?
    /// The last check on the Island in this take: "Footnote this" shows it again when the server has nothing newer.
    private var lastShown: CheckResult?
    private var shownChecks: Set<String> = []
    /// The server's state as the Island shows it (`islandBaseState(from:)`): in checks mode without the transcript, and a
    /// change between listening and paused only after the new mode held for `baseSettleSeconds`.
    private var islandBase = LensActivityAttributes.ContentState.idle(at: .distantPast)
    private var baseCandidate: (mode: LensActivityAttributes.ContentState.Mode, since: Date)?
    private static let baseSettleSeconds = 4.0
    /// The app sends the server's state to the Island (the update path names the app). Results and the lit logo it
    /// always sends.
    private var appOwnsBase = true
    @ObservationIgnored private var releaseTask: Task<Void, Never>?
    /// The server pushes the Island through APNs and its last push arrived (`push.ok` in /api/latest): it is the one
    /// owner, and the app sends no update of its own (not the lit logo, not a turn step, not a result). The app still
    /// starts and ends the Live Activity, keeps the page and does the haptic. Off (no token, APNs fails, an old server,
    /// the simulator): the app updates the Island as before.
    private(set) var serverOwnsIsland = false
    /// The push status of the last poll, for the heartbeat.
    @ObservationIgnored private var lastPush: PushStatus?
    @ObservationIgnored private var tokenResentAt = Date.distantPast
    /// A tap on a result whose claim is not on the page yet: the app polls and opens it when it arrives.
    @ObservationIgnored private var tapWaitRun = 0
    /// footnote://check/<id> from the Island: the page opens that note with its sources.
    var wantsNoteId: Int?
    /// The last press for Debug: the outcome and the time from the press to the answer in the Island.
    private(set) var lastNow = ""
    var card = CardContent(mode: .waiting, label: LensBrand.name, text: "Waiting for audio...", detail: "")

    @ObservationIgnored let pip = PiPController()
    @ObservationIgnored let audio = AudioSessionManager()
    @ObservationIgnored let activity = LiveActivityController()
    @ObservationIgnored let picker = BroadcastPickerHost()
    @ObservationIgnored private let poller = ResultPoller()
    @ObservationIgnored private var heartbeatTimer: Timer?
    @ObservationIgnored private let defaults = UserDefaults.standard
    @ObservationIgnored private var pipSetUp = false
    @ObservationIgnored private let store = SessionStore()
    @ObservationIgnored private var signalAt: Date?
    @ObservationIgnored private var wasLive = false

    let buildBranch = Bundle.main.object(forInfoDictionaryKey: "PhoneBuildBranch") as? String ?? ""
    let buildCommit = Bundle.main.object(forInfoDictionaryKey: "PhoneBuildCommit") as? String ?? ""
    let buildTime = Bundle.main.object(forInfoDictionaryKey: "PhoneBuildTime") as? String ?? ""
    let apsEnvironment = ProvisioningInfo.apsEnvironment

    init() {
        let d = UserDefaults.standard
        updatePath = UpdatePath(rawValue: d.string(forKey: "updatePath") ?? "") ?? .auto
        chunkSeconds = d.object(forKey: "chunkSeconds") as? Double ?? 4
        activityAlerts = d.object(forKey: "activityAlerts") as? Bool ?? true
        alertSound = d.object(forKey: "alertSound") as? Bool ?? false
        techniqueNotes = d.bool(forKey: "techniqueNotes")
        keepAlive = d.object(forKey: "keepAlive") as? Bool ?? true
        screenPath = ScreenPath(rawValue: d.string(forKey: "screenPath") ?? "") ?? .jpeg
        fps = d.object(forKey: "fps") as? Double ?? 1
        tiktokOnly = d.bool(forKey: "tiktokOnly")
        speechPath = SpeechPath(rawValue: d.string(forKey: "speechPath") ?? "") ?? .upload
        pipEnabled = d.object(forKey: "pipEnabled") as? Bool ?? false
        audioVariant = AudioVariant(rawValue: d.string(forKey: "audioVariant") ?? "") ?? .playbackMix
        mixAfterPiPStart = d.object(forKey: "mixAfterPiPStart") as? Bool ?? false
        deactivateAfterPiPStart = d.object(forKey: "deactivateAfterPiPStart") as? Bool ?? false
        autoPiPFromInline = d.object(forKey: "autoPiPFromInline") as? Bool ?? false
        listening = d.object(forKey: "listening") as? Bool ?? false
        onboarded = d.bool(forKey: "onboarded")
        broadcastConfirmed = d.bool(forKey: "broadcastConfirmed")
        sessions = store.sessions
        // The last session stays on the page after a relaunch (also when an install killed the app).
        current = sessions.first

        log("Lens launched, build \(buildBranch) \(buildCommit.prefix(8)) \(buildTime), aps-environment \(apsEnvironment ?? "none")")
        // A relaunch (for example after an install) while a run was on: keep the app alive again.
        if listening && activity.isRunning {
            audio.apply(audioVariant)
            applyKeepAlive()
        } else if listening {
            listening = false
            d.set(false, forKey: "listening")
        }
        // A session the app did not stop (an install or a crash killed it) ends at its last line.
        if !listening, var open = current, open.endedAt == nil {
            open.endedAt = open.items.last?.at ?? open.startedAt
            current = open
            store.upsert(open)
            sessions = store.sessions
        }

        activity.onPushToken = { [weak self] token in self?.sendConfig(extra: ["pushToken": token]) }
        pip.onChange = { [weak self] in self?.refreshPiPState() }
        pip.onDidStart = { [weak self] in self?.afterPiPStarted() }
        poller.onResponse = { [weak self] response, new, newChecks in self?.handle(response, new: new, newChecks: newChecks) }
        poller.onFailure = { [weak self] in self?.consecutiveFailures += 1 }
        observeBroadcastSignals()
        poller.start()
        activityState = activity.stateDescription
        sendConfig()

        if d.bool(forKey: "lensBench") { runBenchmark() }
        if LensConfig.follow {
            log("follow mode: session \(LensConfig.session)")
            onboarded = true
            if !listening { startListening(showPicker: false) }
        }

        let timer = Timer(timeInterval: 2, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.heartbeat() }
        }
        RunLoop.main.add(timer, forMode: .common)
        heartbeatTimer = timer

        let center = NotificationCenter.default
        for (name, label) in [
            (UIApplication.didEnterBackgroundNotification, "app entered background"),
            (UIApplication.willEnterForegroundNotification, "app will enter foreground"),
            (UIApplication.willResignActiveNotification, "app will resign active"),
            (UIApplication.didBecomeActiveNotification, "app became active"),
        ] {
            center.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated {
                    log(label)
                    self?.heartbeat()
                }
            }
        }
    }

    // MARK: Start and stop

    /// One tap: audio session and silent audio, a new run on the server, a new Live Activity, the broadcast picker.
    func startListening(showPicker: Bool = true) {
        prepareRun()
        Task { await startActivity() }
        if showPicker && !broadcastLive {
            picker.show()
        }
    }

    /// Start listening from an intent (Control, Action button, Shortcuts): no picker, the app may be in the background.
    func startListeningInBackground() async {
        prepareRun()
        await startActivity()
        log("listening started from an intent; broadcast \(broadcastLive ? "on" : "off")")
    }

    private func startActivity() async {
        let first: LensActivityAttributes.ContentState = broadcastLive ? .idle() : Self.waitingState()
        await activity.start(state: first, wantPush: apsEnvironment != nil)
        activityState = activity.stateDescription
        poller.poll()
    }

    static func waitingState() -> LensActivityAttributes.ContentState {
        var s = LensActivityAttributes.ContentState.idle()
        s.mode = .waiting
        return s
    }

    private func prepareRun() {
        listening = true
        demo = nil
        freshItemId = nil
        broadcastEnded = false
        wasLive = broadcastLive
        let session = StoredSession(startedAt: .now)
        current = session
        store.upsert(session)
        sessions = store.sessions
        audio.apply(audioVariant)
        applyKeepAlive()
        sendConfig(extra: ["start": true, "running": true])
    }

    func stopListening() {
        listening = false
        broadcastEnded = false
        if var session = current {
            session.endedAt = .now
            current = session
            store.upsert(session)
            sessions = store.sessions
        }
        activity.end()
        applyKeepAlive()
        sendConfig(extra: ["running": false])
        activityState = "ended"
        if broadcastLive { picker.show() }
    }

    func showBroadcastPicker() {
        picker.show()
    }

    private func applyKeepAlive() {
        let on = keepAlive && listening
        if on && !audio.silentAudioRunning { audio.apply(audioVariant) }
        audio.setSilentAudio(on)
    }

    private func sendConfig(extra: [String: Any] = [:]) {
        var body: [String: Any] = [
            "session": LensConfig.session,
            "updatePath": updatePath.rawValue,
            "chunkSeconds": chunkSeconds,
            "alerts": techniqueAlerts,
            "alertSound": alertSound,
            "keepAlive": keepAlive,
            "apsEnvironment": apsEnvironment ?? "",
            "screenPath": screenPath.rawValue,
            "fps": fps,
            "apps": tiktokOnly ? "tiktok" : "any",
            "speechPath": speechPath.rawValue,
        ]
        if let token = activity.pushToken { body["pushToken"] = token }
        body.merge(extra) { _, new in new }
        ServerPost.json("api/config", body)
    }

    // MARK: Results and the Live Activity

    /// Sends a fake transcript through the server, so every update path sees it. No router, no credits.
    func simulate(technique: Bool) {
        let text = technique
            ? "Only three spots left, this deal ends tonight, so act now before it is gone"
            : "So today I want to show you my morning routine and the three things I always do"
        ServerPost.json("api/fake", ["text": text, "session": LensConfig.session])
    }

    /// Runs the extension's screen pipeline on a bundled TikTok screenshot and logs time and memory per step.
    func runBenchmark() {
        guard !benchmarkRunning else { return }
        benchmarkRunning = true
        Task.detached(priority: .userInitiated) {
            let lines = ScreenBenchmark.run()
            await MainActor.run {
                self.benchmark = lines
                self.benchmarkRunning = false
                for line in lines { log(line) }
                Log.shared.flush()
            }
        }
    }

    private func handle(_ response: LatestResponse, new: [TranscriptResult], newChecks: [CheckResult] = []) {
        serverOK = true
        broadcast = response.broadcast
        nowPlaying = response.screen
        consecutiveFailures = 0
        let beatAge = response.broadcast?.ts.map { response.server_time - $0 } ?? .infinity
        let beatLive = beatAge < 6 && response.broadcast?.state == "running"
        // The extension's Darwin signal arrives before its first heartbeat reaches the server.
        let signalLive = signalAt.map { Date().timeIntervalSince($0) < 10 } ?? false
        broadcastLive = beatLive || signalLive
        noteBroadcastChange()
        updateIslandOwner(response.push)
        for result in new {
            lastResultId = max(lastResultId, result.id)
            log("result #\(result.id)\(result.technique.map { " [\($0)]" } ?? ""): \(result.text.prefix(80))")
        }
        if var next = response.state {
            // Listening is on but the broadcast does not run: the Island asks to start it.
            if listening && !broadcastLive && next.mode == .idle { next.mode = .waiting }
            if next.mode == .checked || (!techniqueNotes && (next.mode == .found || next.mode == .nothing || next.mode == .reading)) {
                // Footnote checks facts: a technique note is not shown on the Island. The app shows checks itself
                // (with their hold), so the server's "checked" state is plain listening here.
                next.mode = .listening
                next.flagText = nil; next.flagMark = nil; next.flagLabel = nil; next.flagShort = nil; next.flagWhy = nil
                next.checkLine = nil; next.checkSource = nil; next.checkOf = nil; next.noteId = nil
            }
            next.quietNotes = techniqueAlerts ? nil : true
            state = next
            islandBase = islandBaseState(from: next)
        }
        ingest(response, new: new, newChecks: newChecks)

        pushReady = apsEnvironment != nil && activity.pushToken != nil

        let extensionUpdating = broadcastLive && response.broadcast?.updatePathActive == "extension"
        let appUpdates: Bool
        let appAlerts: Bool
        switch updatePath {
        case .auto:
            appUpdates = true
            appAlerts = !extensionUpdating
            pathActive = extensionUpdating ? "extension+app" : "app"
        case .app:
            appUpdates = true
            appAlerts = true
            pathActive = "app"
        case .fromExtension:
            appUpdates = false
            appAlerts = false
            pathActive = extensionUpdating ? "extension" : "extension (not updating)"
        case .push:
            appUpdates = false
            appAlerts = false
            pathActive = pushReady ? "push" : "push (no token)"
        }
        if serverOwnsIsland { pathActive = "server push" }
        if !activity.isRunning { pathActive = "none (no Live Activity)" }
        appOwnsBase = appUpdates
        pushIsland(alerts: appAlerts && techniqueAlerts)
        activityState = activity.stateDescription
        if pipEnabled { refreshCard() }
    }

    /// One owner of the Island: the server while its APNs pushes arrive, else the app. Sends the push token again
    /// when the server lost it (at most once each 10 s).
    private func updateIslandOwner(_ push: PushStatus?) {
        lastPush = push
        let owns = push?.serverOwnsIsland == true
        if owns != serverOwnsIsland {
            serverOwnsIsland = owns
            log(owns ? "island owner: server push" : "island owner: app")
            // The Island shows the server's last push now: the app's next state must go out, also when it equals
            // the state the app sent before.
            if !owns { activity.sink.reset() }
        }
        if push?.tokenSet == false, let token = activity.pushToken, Date().timeIntervalSince(tokenResentAt) >= 10 {
            tokenResentAt = Date()
            log("push token: the server has none, sending \(token.prefix(8))... again")
            sendConfig(extra: ["pushToken": token])
        }
    }

    /// Adds the results of this run (after the server's startId) to the page, without repeated lines.
    private func ingest(_ response: LatestResponse, new: [TranscriptResult], newChecks: [CheckResult]) {
        guard listening, var session = current else { return }
        let startId = response.config?.startId ?? 0
        let newIds = Set(new.map(\.id))
        var added = false
        var freshAdded = false
        for var r in response.results where r.id > startId {
            // The server names the app in the screen read, not in each result: fill it for the same video.
            if r.app == nil, let screen = response.screen, screen.videoId != nil, screen.videoId == r.videoId { r.app = screen.app }
            if session.add(r) {
                added = true
                if newIds.contains(r.id) { freshAdded = true }
            }
        }
        // Checks come back later, as their own items or on the sentence. All of them become quiet footnotes on the
        // page. A new pending check lights the logo. A new result dims it again; only a result the server marks notify
        // (contradicted by sources), forced or asked for with "Footnote this" goes to the Island, once.
        let nested = response.results.compactMap { r in r.check.map { CheckResult(id: nil, of: r.id, claim: r.text, check: $0, forced: r.forced) } }
        let newCheckKeys = Set(newChecks.map(\.key)).union(new.compactMap { r in r.check.map { CheckResult(id: nil, of: r.id, claim: nil, check: $0).key } })
        for c in response.checks + nested where (c.of ?? Int.max) > startId {
            // A result the Island shows (forced, or contradicted by sources) always gets its line, so a tap on the
            // Island finds it: also a claim read from the screen text, which has no sentence (`of` is nil).
            let changed = session.apply(c) != nil
                || ((c.forced == true || c.check.notifies) && session.index(of: c) == nil && session.addClaim(c) != nil)
            if changed {
                added = true
                log("check of #\(c.of ?? -1): \(c.check.status.rawValue) \(c.check.stance?.rawValue ?? "-")\(c.check.notify == true ? " notify" : "")\(c.check.line.map { ": \($0)" } ?? "")")
            }
            guard newCheckKeys.contains(c.key), let claim = c.of ?? c.id else { continue }
            if !c.check.isDone {
                if !techniqueNotes { claimFound(claim) }
                continue
            }
            pendingChecks[claim] = nil
            let asked = nowWanted.remove(claim) != nil
            if c.check.notifies || c.forced == true || asked {
                showCheck(c, in: session, screen: response.screen, again: asked)
            }
        }
        guard added else { return }
        current = session
        if freshAdded { freshItemId = session.items.last?.id }
        store.upsert(session)
        sessions = store.sessions
    }

    /// A check came back: a footnote on the Island and the Lock Screen. When the user scrolled on, it says which
    /// earlier video the claim is from.
    private func showCheck(_ c: CheckResult, in session: StoredSession, screen: NowPlaying?, again: Bool = false) {
        // A new item (a new play of the video in a new take) shows again; the same item only once.
        let key = c.id.map { "item-\($0)" } ?? c.key
        guard activity.isRunning, again || !shownChecks.contains(key) else { return }
        shownChecks.insert(key)
        lastShown = c
        let item = c.of.flatMap { id in session.items.last { $0.resultId == id } }
        let state = Self.checkedState(c, item: item, in: session, base: islandBase, videoInFront: screen?.videoId)
        BroadcastSignal.holdIsland.post()
        show(state, for: 10, alert: true)
        Self.haptic()
        log("island: \(c.check.shortLabel) for #\(c.of ?? -1)")
    }

    /// The Island state of a check: the claim, the stance in words, the line, the first source, and the earlier
    /// video when it is not the one in front.
    static func checkedState(_ c: CheckResult, item: PageItem?, in session: StoredSession, base: LensActivityAttributes.ContentState,
                             videoInFront: Int?) -> LensActivityAttributes.ContentState {
        var s = base
        s.mode = .checked
        s.updatedAt = .now
        let claim = item?.claim ?? c.claim ?? item?.text ?? ""
        s.flagText = String(claim.trimmingCharacters(in: .whitespacesAndNewlines).prefix(120))
        s.flagMark = nil
        s.flagLabel = c.check.label
        s.flagShort = c.check.shortLabel
        s.flagWhy = nil
        s.checkLine = c.check.line.map { String($0.prefix(90)) }
        s.checkSource = c.check.sources.prefix(2).map(\.title).joined(separator: ", ").nilIfEmpty
        s.noteId = c.of ?? item?.resultId
        let video = item?.videoId ?? c.videoId
        let scrolledOn = video != nil && videoInFront != nil && video != videoInFront
        let handle = (item?.handle ?? c.handle).map { $0.hasPrefix("@") || $0.contains(" ") ? $0 : "@" + $0 }
        s.checkOf = scrolledOn ? LensBrand.earlierVideo(handle ?? item.flatMap { session.origin(of: $0) }) : nil
        return s
    }

    private func noteBroadcastChange() {
        if broadcastLive {
            wasLive = true
            broadcastEnded = false
            if !broadcastConfirmed { broadcastConfirmed = true }
        } else if listening && wasLive {
            broadcastEnded = true
            wasLive = false
        }
    }

    /// Listens for the extension's Darwin notifications (no App Group needed).
    private func observeBroadcastSignals() {
        let center = CFNotificationCenterGetDarwinNotifyCenter()
        let observer = Unmanaged.passUnretained(self).toOpaque()
        for signal in [BroadcastSignal.started, .finished, .footnoteNowRequest, .newTakeRequest] {
            CFNotificationCenterAddObserver(center, observer, { _, observer, name, _, _ in
                guard let observer, let name else { return }
                let raw = name.rawValue as String
                let model = Unmanaged<AppModel>.fromOpaque(observer).takeUnretainedValue()
                DispatchQueue.main.async {
                    MainActor.assumeIsolated { model.broadcastSignal(raw) }
                }
            }, signal.rawValue as CFString, nil, .deliverImmediately)
        }
    }

    private func broadcastSignal(_ raw: String) {
        log("broadcast signal: \(raw)")
        switch BroadcastSignal(rawValue: raw) {
        case .footnoteNowRequest?: Task { await footnoteNow() }; return
        case .newTakeRequest?: Task { await newTake() }; return
        default: break
        }
        if raw == BroadcastSignal.started.rawValue {
            signalAt = Date()
            broadcastLive = true
            speechPath.signal.post()
            (techniqueNotes ? BroadcastSignal.techniqueOn : .techniqueOff).post()
        } else {
            signalAt = nil
            broadcastLive = false
        }
        noteBroadcastChange()
        poller.poll()
    }

    /// What leaves the phone, exact for the speech path and the screen path in use.
    var leavesPhone: String {
        let server = "the \(LensBrand.name) server"
        let kept = " Nothing is saved but the words on this page."
        switch (speechPath, screenPath) {
        case (.upload, .jpeg): return "What leaves the phone: short sound clips and small grey screen images go to \(server). It reads them and drops them." + kept
        case (.upload, .off): return "What leaves the phone: short sound clips go to \(server). The screen is not read." + kept
        case (.upload, _): return "What leaves the phone: short sound clips and the text read from the screen go to \(server). No screen image leaves the phone." + kept
        case (.onDevice, .jpeg): return "What leaves the phone: the phone turns speech into text. Only that text and small grey screen images go to \(server). No sound leaves the phone." + kept
        case (.onDevice, .off): return "What leaves the phone: only the text of the speech, which the phone writes itself. No sound or image leaves the phone." + kept
        case (.onDevice, _): return "What leaves the phone: only text. The phone turns speech and the words on the screen into text. No sound or image leaves the phone." + kept
        case (.auto, .jpeg): return "What leaves the phone: short sound clips at the start of each video, then only the text of the speech when the phone knows the language. Small grey screen images go to \(server) too." + kept
        case (.auto, _): return "What leaves the phone: short sound clips at the start of each video, then only the text of the speech when the phone knows the language. No screen image leaves the phone." + kept
        }
    }

    func refreshSpeechModels(install: Bool) async {
        speechModels = install ? "Downloading..." : speechModels
        speechModels = install ? await SpeechModels.install() : await SpeechModels.status()
    }

    // MARK: Footnote this

    /// The Action button, the Control, the Island button and the page button: "deliver now". The logo lights at once.
    /// POST /api/now returns the held check: a result expands the Island whatever its stance, a pending check keeps the
    /// logo lit until its result arrives in the stream, and nothing gives two turn steps only. No words on the Island.
    /// With technique notes on (Debug), the old "Reading..." and "No note." flow.
    func footnoteNow() async {
        nowPresses += 1
        let press = Date()
        Self.haptic()
        BroadcastSignal.holdIsland.post()
        if !listening || !activity.isRunning {
            if !listening && demo == nil { prepareRun() }
            await startActivity()
        }
        if demo != nil, let held = demoHeld {
            // The demo replay: deliver its held check, without the server.
            demoHeld = nil
            deliverDemo(held)
            return
        }
        if !techniqueNotes, !broadcastLive, let newest = newestFinishedCheck() {
            // No broadcast, but this session has a result: a press always ends in it.
            showCheck(newest, in: shown ?? StoredSession(startedAt: .now), screen: nowPlaying, again: true)
            lastNow = "broadcast off: newest check of this session shown"
            log("footnote this: \(lastNow)")
            return
        }
        guard broadcastLive else {
            show(Self.waitingState(), for: 6, alert: true)
            lastNow = "broadcast off: asked to start it"
            log("footnote this: the broadcast does not run")
            return
        }
        if techniqueNotes {
            var reading = state
            reading.mode = .reading
            reading.updatedAt = press
            show(reading, for: 15, alert: true)
        } else {
            lightUp(minimum: 2.2)
        }

        let outcome = await NowClient.ask(lastResultId: lastResultId)
        if case .check(let c) = outcome {
            answerCheck(c, press: press)
            return
        }
        if !techniqueNotes, let newest = newestFinishedCheck() {
            // The server has nothing newer, or did not answer: the newest result this app received in the session,
            // whatever video is on now. A press always ends in a result when the session has one.
            showCheck(newest, in: shown ?? StoredSession(startedAt: .now), screen: nowPlaying, again: true)
            lastNow = "newest check of this session shown (server: \(Self.describe(outcome)))"
            log("footnote this: \(lastNow)")
            sendConfig(extra: ["nowPress": ["ms": Int(Date().timeIntervalSince(press) * 1000), "outcome": lastNow]])
            return
        }
        if techniqueNotes {
            // "Reading..." stays long enough to be seen; a quick answer would replace it before the Island has drawn it.
            let shown = Date().timeIntervalSince(press)
            if shown < 0.6 { try? await Task.sleep(for: .milliseconds(Int((0.6 - shown) * 1000))) }
            let answer = Self.answerState(outcome, base: state)
            BroadcastSignal.holdIsland.post()
            show(answer, for: 8, alert: true)
        }
        // Checks only: nothing to deliver. The logo turns two steps, then dims by itself.
        let ms = Int(Date().timeIntervalSince(press) * 1000)
        switch outcome {
        case .result(let r, let fallback):
            lastNow = "\(r.technique ?? (r.unsure == true ? "unsure" : "line")) in \(ms) ms\(fallback ? " (no /api/now: newest result)" : "")"
            log("footnote this: \(lastNow): \(r.text.prefix(60))")
            // A forced note belongs on the page too.
            if techniqueNotes, listening, var session = current, session.add(r) {
                current = session
                freshItemId = session.items.last?.id
                store.upsert(session)
                sessions = store.sessions
            }
        case .nothing(let reason, _, let fallback):
            lastNow = "nothing (\(reason)) in \(ms) ms\(fallback ? " (no /api/now: newest result)" : "")"
            log("footnote this: \(lastNow)")
        case .failed(let error):
            lastNow = "failed in \(ms) ms: \(error)"
            log("footnote this: \(lastNow)")
        case .check:
            break
        }
        sendConfig(extra: ["nowPress": ["ms": ms, "outcome": lastNow]])
    }

    /// "New take": the server forgets this session's claims and checks (POST /api/reset), the page starts a new
    /// session, and the Island goes back to the dim listening state. The broadcast keeps running.
    func newTake() async {
        ServerPost.json("api/reset", ["session": LensConfig.session]) { ok in log("new take: /api/reset \(ok ? "ok" : "failed")") }
        demoRun += 1
        demoHeld = nil
        if demo != nil { demo = nil }
        pendingChecks = [:]
        nowWanted = []
        shownChecks = []
        lastShown = nil
        lastResultId = 0
        poller.reset()
        settleIsland()
        if nowState?.mode == .checked { nowState = nil; nowUntil = nil }
        pushIsland()
        if activity.isRunning, UIApplication.shared.applicationState == .active {
            // A new Live Activity starts with a fresh update budget (iOS starts one only while the app is in front).
            await startActivity()
            pushIsland(important: true)
        }
        if listening {
            let session = StoredSession(startedAt: .now)
            current = session
            store.upsert(session)
            sessions = store.sessions
        }
        freshItemId = nil
        lastNow = "new take"
        Self.haptic()
        log("new take")
    }

    /// "Footnote this" got a check. A result goes to the Island at once, whatever the stance. A pending check keeps
    /// the logo lit until the result arrives with the poll (at most 60 s), and that result goes to the Island too.
    private func answerCheck(_ c: CheckResult, press: Date) {
        let ms = Int(Date().timeIntervalSince(press) * 1000)
        if var session = current, listening, session.apply(c) != nil || session.addClaim(c) != nil {
            current = session
            store.upsert(session)
            sessions = store.sessions
        }
        let claim = c.of ?? c.id
        if c.check.isDone {
            lastNow = "check \(c.check.stance?.rawValue ?? c.check.status.rawValue) in \(ms) ms"
            if let claim { pendingChecks[claim] = nil }
            showCheck(c, in: shown ?? StoredSession(startedAt: .now), screen: nowPlaying, again: true)
        } else {
            lastNow = "check pending after \(ms) ms"
            if let claim {
                nowWanted.insert(claim)
                claimFound(claim)
            }
        }
        log("footnote this: \(lastNow)")
        sendConfig(extra: ["nowPress": ["ms": ms, "outcome": lastNow]])
    }

    // MARK: The lit logo

    /// A claim is under check: the logo lights up and stays lit until its result comes, at most 60 s.
    private func claimFound(_ claim: Int) {
        if pendingChecks[claim] == nil { log("claim #\(claim): checking") }
        pendingChecks[claim] = pendingChecks[claim] ?? Date()
        lightUp(minimum: 0)
    }

    /// The logo is lit while a claim is under check, for its short minimum, or while the scripted take waits for
    /// "Footnote this".
    private var isLit: Bool {
        let now = Date()
        return now < litUntil || pendingChecks.values.contains { now.timeIntervalSince($0) < 60 }
    }

    /// Lights the logo: the Island shows the checking state (no words, no alert) and the asterisk glows in marker
    /// yellow. A Live Activity runs no repeating animation, so the app sends one turn step about each second: `turn`
    /// counts up, and the view turns one arm spacing forward for each step.
    private func lightUp(minimum: Double) {
        litUntil = max(litUntil, Date().addingTimeInterval(minimum))
        guard activity.isRunning else { return }
        BroadcastSignal.holdIsland.post()
        pushIsland(important: true)
        startTurning()
    }

    private var turnTask: Task<Void, Never>?
    /// The turn steps of the lit asterisk so far. It only counts up, so the asterisk never turns back.
    private var turnCount = 0

    /// Which turn task is the current one: a cancelled task that wakes late must not clear a newer one (two tasks
    /// sent two updates per second on the phone).
    private var turnRun = 0

    private func startTurning() {
        guard turnTask == nil else { return }
        turnRun += 1
        let run = turnRun
        turnTask = Task { @MainActor in
            var beats = 0
            var steps = 0
            defer { if self.turnRun == run { self.turnTask = nil } }
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(LensActivityAttributes.stepSeconds))
                guard !Task.isCancelled else { return }
                guard self.isLit else {
                    self.pendingChecks = [:]
                    self.logoWentOut()
                    return
                }
                beats += 1
                // The extension holds the Island for 12 s per signal: keep it held while the logo is lit.
                if beats % 9 == 0 { BroadcastSignal.holdIsland.post() }
                // A result is on the Island: it keeps its hold, and the logo turns again after it.
                if self.resultOnIsland { continue }
                // iOS drops Live Activity updates from an app in the background once it sent many: lit is one
                // update, then at most 8 turn steps, 4 s apart, then the steady glow.
                guard beats % 4 == 0, steps < 8 else { continue }
                steps += 1
                self.turnCount += 1
                self.pushIsland()
            }
        }
    }

    /// The logo went out. A press that waited for a check that never came still ends in a result, when this session
    /// has one.
    private func logoWentOut() {
        if !nowWanted.isEmpty, !resultOnIsland, let newest = newestFinishedCheck() {
            nowWanted = []
            showCheck(newest, in: shown ?? StoredSession(startedAt: .now), screen: nowPlaying, again: true)
            log("footnote this: the check did not come, newest check of this session shown")
            return
        }
        nowWanted = []
        pushIsland()
    }

    /// The logo dims and any result leaves: the Island shows the server's state again.
    private func settleIsland() {
        turnTask?.cancel()
        turnTask = nil
        turnRun += 1
        litUntil = .distantPast
        nowUntil = nil
        nowState = nil
        pushIsland()
    }

    /// A result (or an answer, or the start prompt) is on the Island and its hold has not ended.
    private var resultOnIsland: Bool {
        guard nowState != nil, let until = nowUntil else { return false }
        return Date() < until
    }

    /// Puts a result on the Island and holds it there: a check result at least 12 s, or until a tap or New take.
    private func show(_ s: LensActivityAttributes.ContentState, for seconds: Double, alert: Bool) {
        nowState = s
        nowUntil = Date().addingTimeInterval(s.mode == .checked ? max(seconds, 12) : seconds)
        pushIsland(force: alert, important: true)
    }

    // MARK: The one owner of the Island

    /// What the Island shows now (Debug > Island now).
    var islandNow: LensActivityAttributes.ContentState { islandState() }

    /// What the Island shows now, by priority: a result in its hold > the lit logo (checking) > the server's state
    /// (paused, listening and the others, settled in `islandBaseState(from:)`). A lower state never replaces a
    /// higher one before its hold ends.
    private func islandState() -> LensActivityAttributes.ContentState {
        if resultOnIsland, let s = nowState { return s }
        if isLit {
            var s = islandBase
            s.mode = .checking
            s.turn = turnCount
            s.flagText = nil; s.flagMark = nil; s.flagLabel = nil; s.flagShort = nil; s.flagWhy = nil
            return s
        }
        return islandBase
    }

    /// Sends `islandState()` to the Live Activity. Every change of the Island goes through here. The sink sends
    /// nothing when the state did not change, so a poll with nothing new costs no update.
    private func pushIsland(alerts: Bool = false, force: Bool = false, important: Bool = false) {
        if nowState != nil, !resultOnIsland {
            nowState = nil
            nowUntil = nil
        }
        guard activity.isRunning, !serverOwnsIsland else { return }
        if !resultOnIsland, !isLit, !appOwnsBase { return }
        let sent = islandState()
        activity.update(sent, alerts: alerts || force, sound: alertSound, force: force)
        scheduleRelease()
        if important { retryIfDropped(sent, force: force) }
    }

    /// The lit logo and a result matter most: when the activity still reads back an older state after 1.2 s, send
    /// the same state once more.
    private func retryIfDropped(_ sent: LensActivityAttributes.ContentState, force: Bool) {
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(1200))
            guard self.activity.isRunning, !self.serverOwnsIsland, self.islandState() == sent,
                  let back = self.activity.current?.content.state, back.mode != sent.mode || back.turn != sent.turn else { return }
            log("island: the activity reads back \(back.mode.rawValue), sending \(sent.mode.rawValue) again")
            self.activity.sink.reset()
            self.activity.update(sent, alerts: force, sound: self.alertSound, force: force)
        }
    }

    /// When a hold ends, the next state goes to the Island at once, not at the next poll.
    private func scheduleRelease() {
        releaseTask?.cancel()
        guard resultOnIsland, let until = nowUntil else { releaseTask = nil; return }
        releaseTask = Task { @MainActor in
            try? await Task.sleep(for: .seconds(max(0.05, until.timeIntervalSinceNow + 0.05)))
            guard !Task.isCancelled else { return }
            self.releaseTask = nil
            self.pushIsland()
        }
    }

    /// The server's state for the Island. In checks mode the Island shows no transcript, so the line, the counts and
    /// the time are left out: a new line is no reason for an update. A change between listening and paused counts
    /// only after it held for `baseSettleSeconds` (the screen read can flip for a frame).
    private func islandBaseState(from next: LensActivityAttributes.ContentState) -> LensActivityAttributes.ContentState {
        var s = next
        if !techniqueNotes {
            s.line = ""
            s.count = 0
            s.chunks = 0
            s.videoNotes = nil
            s.updatedAt = Date(timeIntervalSinceReferenceDate: 0)
            s.flagText = nil; s.flagMark = nil; s.flagLabel = nil; s.flagShort = nil; s.flagWhy = nil
        }
        let flips: Set<LensActivityAttributes.ContentState.Mode> = [.listening, .paused]
        let old = islandBase.mode
        guard s.mode != old, flips.contains(s.mode), flips.contains(old) else {
            baseCandidate = nil
            return s
        }
        let now = Date()
        if baseCandidate?.mode != s.mode { baseCandidate = (s.mode, now) }
        if let since = baseCandidate?.since, now.timeIntervalSince(since) >= Self.baseSettleSeconds {
            baseCandidate = nil
            return s
        }
        // Not settled yet: the old mode stays, with the new handle.
        var kept = s
        kept.mode = old
        return kept
    }

    /// The newest finished check this app received in the session: the one on the Island last, or the newest
    /// sentence on the page with a finished check.
    private func newestFinishedCheck() -> CheckResult? {
        let items = shown?.items ?? []
        if let last = lastShown, last.of == nil || !items.contains(where: { $0.resultId == last.of }) { return last }
        guard let item = items.last(where: { $0.check?.isDone == true }), let check = item.check else { return lastShown }
        if let last = lastShown, last.of == item.resultId { return last }
        return CheckResult(id: nil, of: item.resultId, claim: item.claim, check: check, forced: true, handle: item.handle, videoId: item.videoId)
    }

    private static func describe(_ outcome: NowClient.Outcome) -> String {
        switch outcome {
        case .result: return "a line"
        case .check: return "a check"
        case .nothing(let reason, _, _): return "nothing (\(reason))"
        case .failed(let error): return "failed: \(error)"
        }
    }

    static func answerState(_ outcome: NowClient.Outcome, base: LensActivityAttributes.ContentState) -> LensActivityAttributes.ContentState {
        var s = base
        s.updatedAt = .now
        s.flagMark = nil
        switch outcome {
        case .result(let r, _):
            let text = r.text.trimmingCharacters(in: .whitespacesAndNewlines)
            if let technique = r.technique, r.unsure != true {
                let brand = LensBrand.technique(technique)
                s.mode = .found
                s.flagText = String(text.prefix(120))
                s.flagMark = r.mark
                s.flagLabel = r.label ?? brand?.label ?? technique
                s.flagShort = r.short ?? brand?.short ?? r.label
                s.flagWhy = r.why ?? brand?.why
            } else {
                s.mode = .nothing
                s.flagText = String(text.prefix(120))
                s.flagLabel = LensBrand.Words.notSure
                s.flagShort = nil
                s.flagWhy = r.why ?? "It may lean one way, but the signal is weak."
            }
        case .nothing(let reason, let line, _):
            s.mode = .nothing
            let heard = line ?? (base.line.isEmpty ? nil : base.line)
            s.flagText = heard.map { String($0.suffix(120)) }
            s.flagLabel = LensBrand.Words.noNote
            s.flagShort = nil
            switch reason {
            case "paused", "private": s.flagWhy = LensBrand.Words.paused + "."
            case "silence", "no speech", "nothing heard": s.flagWhy = LensBrand.Words.nothingHeard
            default: s.flagWhy = heard == nil ? LensBrand.Words.nothingHeard : LensBrand.Words.noNoteWhy
            }
        case .failed:
            s.mode = .nothing
            s.flagText = nil
            s.flagLabel = LensBrand.Words.noNote
            s.flagShort = nil
            s.flagWhy = "The server did not answer. Try again."
        case .check:
            break
        }
        return s
    }

    /// A short tap you feel. In the foreground through UIKit; in the background the system sound, which plays while
    /// the keep-alive audio runs.
    private static func haptic() {
        if UIApplication.shared.applicationState == .active {
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        } else {
            AudioServicesPlaySystemSound(1519)
        }
    }

    /// footnote://start (the Island in its waiting state) opens the start step; footnote://now presses Footnote this.
    func open(_ url: URL) {
        log("open url \(url.absoluteString)")
        switch url.host() {
        case "start":
            if listening && broadcastLive { return }
            Task { @MainActor in
                // The picker must be in the window before its button can be tapped in code.
                try? await Task.sleep(for: .milliseconds(700))
                if !self.listening && !self.broadcastConfirmed {
                    self.wantsGuide = true
                } else if !self.listening {
                    self.startListening()
                } else {
                    self.picker.show()
                }
            }
        case "now":
            Task { await footnoteNow() }
        case "demo":
            // footnote://demo: the demo flow, as Debug > Play the demo flow.
            replayDemoFlow()
        case "check":
            if let id = Int(url.lastPathComponent) { openNote(id) }
            // A tap on the result ends its hold.
            if resultOnIsland { nowUntil = Date(); pushIsland() }
        default:
            break
        }
    }

    /// Opens the claim of a tapped result with its sources. A pushed result can arrive before the poll brings its
    /// claim to the page: then the app polls at once and opens the claim when it arrives, at most 3 s later. After
    /// that the page opens the newest checked claim (HomeView).
    private func openNote(_ id: Int) {
        tapWaitRun += 1
        let run = tapWaitRun
        if id == 0 || shown?.items.contains(where: { $0.matches(noteId: id) }) == true || !listening {
            wantsNoteId = id
            return
        }
        log("open note #\(id): not on the page yet, polling")
        poller.poll()
        Task { @MainActor in
            for step in 1...12 {
                try? await Task.sleep(for: .milliseconds(250))
                guard self.tapWaitRun == run else { return }
                if self.shown?.items.contains(where: { $0.matches(noteId: id) }) == true { break }
                if step % 4 == 0 { self.poller.poll() }
            }
            guard self.tapWaitRun == run else { return }
            let found = self.shown?.items.contains(where: { $0.matches(noteId: id) }) == true
            log("open note #\(id): \(found ? "arrived" : "not found, newest checked claim")")
            self.wantsNoteId = id
        }
    }

    /// The start guide should open (first broadcast on this phone, reached from footnote://start).
    var wantsGuide = false

    // MARK: The home screen

    enum Phase: Equatable {
        case idle, waiting, listening, paused, offline, ended
    }

    var phase: Phase {
        if !listening { return .idle }
        if consecutiveFailures >= 3 { return .offline }
        if broadcastEnded { return .ended }
        if !broadcastLive { return .waiting }
        if state.mode == .paused { return .paused }
        return .listening
    }

    /// The session on the page: the demo from Debug, else this run (also after Stop).
    var shown: StoredSession? { demo ?? current }

    func retry() {
        consecutiveFailures = 0
        poller.poll()
    }

    /// Opens TikTok: the app's own schemes first (both are in LSApplicationQueriesSchemes), then the web.
    func openTikTok() {
        let urls = ["snssdk1233://", "tiktok://", "https://www.tiktok.com/"].compactMap(URL.init(string:))
        func attempt(_ i: Int) {
            guard i < urls.count else { return }
            UIApplication.shared.open(urls[i]) { ok in
                log("open \(urls[i].absoluteString): \(ok)")
                if !ok { Task { @MainActor in attempt(i + 1) } }
            }
        }
        attempt(0)
    }

    func deleteSessions(_ ids: Set<UUID>) {
        store.delete(ids)
        sessions = store.sessions
    }

    func showDemo() {
        demo = DemoSession.make()
        freshItemId = nil
    }

    func clearDemo() {
        demo = nil
        demoHeld = nil
        demoRun += 1
    }

    /// Plays the newest line of the demo again, to see the arrival animation.
    func replayDemoArrival() {
        guard var session = demo, let last = session.items.popLast() else { return }
        demo = session
        freshItemId = nil
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(600))
            session.items.append(last)
            self.demo = session
            self.freshItemId = last.id
        }
    }

    /// Plays the demo flow without the server. The claim on the @davejoneswoke video arrives with "Checking..." on the page,
    /// and the logo lights. After 4 s the user scrolls on to a later video; the logo stays lit while the check is held.
    /// "Footnote this" (the Action button, or Debug) delivers the result: the Island expands with the claim, the
    /// sources and "Earlier video: @davejoneswoke". Without a press, it comes by itself after 20 s, as when the server
    /// delivers it.
    func replayDemoFlow() {
        demoRun += 1
        let run = demoRun
        let start = DemoSession.make(upToClaim: true)
        guard let claim = start.items.first(where: { $0.checkPending }), let claimId = claim.resultId else { return }
        settleIsland()
        demo = start
        freshItemId = start.items.last?.id
        demoHeld = CheckResult(id: nil, of: claimId, claim: DemoSession.shortClaim, check: DemoSession.check, forced: true, handle: claim.handle,
                               videoId: claim.videoId)
        Task { @MainActor in
            // iOS starts a Live Activity only while the app is in front.
            if !self.activity.isRunning { await self.startActivity() }
            self.claimFound(claimId)
            log("demo: claim found, the logo lights")
            try? await Task.sleep(for: .seconds(4))
            guard self.demoRun == run, var session = self.demo else { return }
            // The user scrolls on: the later video starts on the page.
            session.items += DemoSession.later(from: session.items.count)
            self.demo = session
            self.freshItemId = session.items.last?.id
            try? await Task.sleep(for: .seconds(16))
            guard self.demoRun == run, let held = self.demoHeld else { return }
            self.demoHeld = nil
            self.deliverDemo(held)
        }
    }

    /// The demo's check comes back: the second footnote on the page and the expanded Island.
    private func deliverDemo(_ c: CheckResult) {
        guard var session = demo, let i = session.items.lastIndex(where: { $0.resultId == c.of }) else { return }
        session.items[i].check = c.check
        session.items[i].claim = c.claim
        demo = session
        if let claim = c.of { pendingChecks[claim] = nil }
        let front = session.items.last?.videoId
        let state = Self.checkedState(c, item: session.items[i], in: session, base: islandBase, videoInFront: front)
        BroadcastSignal.holdIsland.post()
        show(state, for: 12, alert: true)
        Self.haptic()
        lastNow = "demo: check delivered"
        log("demo: check delivered for \"\(c.claim?.prefix(40) ?? "")\"")
    }

    @ObservationIgnored private var demoRun = 0

    func resetOnboarding() {
        onboarded = false
    }

    // MARK: PiP (debug only)

    func pipLayerReady() {
        guard pipEnabled else { return }
        pip.setUp(autoFromInline: autoPiPFromInline)
        pipSetUp = true
        pip.show(card)
        refreshPiPState()
    }

    private func pipEnabledChanged() {
        if !pipEnabled {
            if pip.isActive { pip.stop() }
            pip.setAutoFromInline(false)
        } else if pipSetUp {
            pip.setAutoFromInline(autoPiPFromInline)
        }
        refreshPiPState()
    }

    func togglePiP() {
        if pip.isActive {
            pip.stop()
            return
        }
        if mixAfterPiPStart && audioVariant.mixes {
            log("variant: start PiP with mix off, turn mix on after start")
            audio.apply(audioVariant, forceMix: false)
        } else {
            audio.apply(audioVariant)
        }
        pip.start()
    }

    private func afterPiPStarted() {
        if mixAfterPiPStart && audioVariant.mixes {
            audio.apply(audioVariant, forceMix: true)
        }
        if deactivateAfterPiPStart {
            log("variant: deactivate audio session after PiP start")
            audio.deactivate()
        }
    }

    private func refreshPiPState() {
        pipPossible = pip.isPossible
        pipActive = pip.isActive
    }

    private func refreshCard() {
        let detail = LensBrand.notes(state.count)
        let next: CardContent
        switch state.mode {
        case .found:
            next = CardContent(mode: .found, label: state.flagLabel ?? "Note", text: state.flagText ?? state.line, detail: detail)
        case .checking:
            next = CardContent(mode: .listening, label: LensBrand.Words.checking, text: state.line, detail: detail)
        case .checked:
            next = CardContent(mode: .found, label: state.flagShort ?? LensBrand.Words.checked, text: state.checkLine ?? state.flagText ?? state.line, detail: detail)
        case .listening, .reading, .nothing:
            next = CardContent(mode: .listening, label: "Listening", text: state.line.isEmpty ? "Waiting for speech..." : state.line, detail: detail)
        case .idle, .waiting:
            next = CardContent(mode: .waiting, label: LensBrand.Words.notListening, text: "Start the broadcast to hear the app in front", detail: detail)
        case .paused:
            next = CardContent(mode: .waiting, label: "Paused", text: "Private screen", detail: detail)
        }
        if next != card {
            card = next
            pip.show(next)
        }
    }

    // MARK: Heartbeat

    private func heartbeat() {
        refreshPiPState()
        activityState = activity.stateDescription
        let app = UIApplication.shared
        let session = AVAudioSession.sharedInstance()
        let phase: String
        switch app.applicationState {
        case .active: phase = "active"
        case .inactive: phase = "inactive"
        case .background: phase = "background"
        @unknown default: phase = "unknown"
        }
        let remaining = app.backgroundTimeRemaining
        // What the Live Activity really shows (read back), so /debug.json proves that the server's pushes arrive.
        let shownState = activity.current?.content.state
        let body: [String: Any] = [
            "activityMode": shownState?.mode.rawValue ?? "",
            "activityTurn": shownState?.turn.map { $0 as Any } ?? NSNull(),
            "activityNoteId": shownState?.noteId.map { $0 as Any } ?? NSNull(),
            "activityFlag": shownState?.flagText.map { String($0.prefix(40)) } ?? "",
            "islandOwner": serverOwnsIsland ? "push" : "app",
            "pushOk": lastPush?.ok.map { $0 as Any } ?? NSNull(),
            "pushTokenSet": lastPush?.tokenSet.map { $0 as Any } ?? NSNull(),
            "source": "app",
            "session": LensConfig.session,
            "state": listening ? "listening" : "stopped",
            "scenePhase": phase,
            "backgroundTimeRemaining": remaining > 1e6 ? "unlimited" : String(format: "%.0f", remaining),
            "updatePath": updatePath.rawValue,
            "updatePathActive": pathActive,
            "activityState": activity.stateDescription,
            "activitiesEnabled": activity.enabled,
            "activityUpdates": activity.sink.updates,
            "activityUpdatesVerified": activity.sink.verified,
            "activityUpdatesInBackground": activity.updatesInBackground,
            "activityAlerts": activity.sink.alerts,
            "activityLastError": activity.startError.isEmpty ? activity.sink.lastError : activity.startError,
            "apsEnvironment": apsEnvironment ?? "none",
            "pushToken": activity.pushToken.map { String($0.prefix(8)) + "..." } ?? "",
            "keepAlive": keepAlive,
            "silentAudio": audio.silentAudioRunning,
            "audioSession": audio.describe(),
            "otherAudioPlaying": session.isOtherAudioPlaying,
            "secondaryAudioShouldBeSilenced": session.secondaryAudioShouldBeSilencedHint,
            "chunkSeconds": chunkSeconds,
            "screenPath": screenPath.rawValue,
            "fps": fps,
            "nowPlayingHandle": nowPlaying?.handle ?? "",
            "lastActivityUpdateAt": activity.lastUpdateAt.map { $0.timeIntervalSince1970 } ?? 0,
            "lastActivityMode": activity.lastUpdateMode,
            "pipEnabled": pipEnabled,
            "pipPossible": pip.isPossible,
            "pipActive": pip.isActive,
            "polls": poller.polls,
            "pollFailures": poller.failures,
            "lastResultId": lastResultId,
            "mode": state.mode.rawValue,
            "count": state.count,
            "vendorId": UIDevice.current.identifierForVendor?.uuidString ?? "",
            "memoryMB": ProcessStats.memoryMB(),
            "build": "\(buildBranch) \(buildCommit.prefix(8))",
        ]
        ServerPost.json("api/heartbeat", body) { [weak self] ok in
            Task { @MainActor in self?.serverOK = ok }
        }
        Log.shared.flush()
    }

    private func save(_ value: Any, _ key: String) {
        defaults.set(value, forKey: key)
    }
}

/// Holds the system broadcast picker. "Start listening" taps its button in code, so there is one big button
/// instead of the small system icon. The view must be in the window for the tap to work.
@MainActor
final class BroadcastPickerHost {
    let view: RPSystemBroadcastPickerView = {
        let picker = RPSystemBroadcastPickerView(frame: CGRect(x: 0, y: 0, width: 44, height: 44))
        picker.preferredExtension = LensConfig.broadcastBundleID
        picker.showsMicrophoneButton = false
        return picker
    }()

    func show() {
        guard let button = view.subviews.compactMap({ $0 as? UIButton }).first else {
            log("broadcast picker: no button found")
            return
        }
        button.sendActions(for: .touchUpInside)
    }
}
