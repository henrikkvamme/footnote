import ActivityKit
import Foundation
import UIKit

/// Starts and ends the Live Activity, and updates it from the app (path B).
@MainActor
final class LiveActivityController {
    let sink = LiveActivitySink(source: "app")
    private(set) var updatesInBackground = 0
    /// When this app last sent an update, and its mode (for the end-to-end timing on /debug).
    private(set) var lastUpdateAt: Date?
    private(set) var lastUpdateMode = ""
    private(set) var pushToken: String?
    private(set) var startError = ""
    var onPushToken: ((String) -> Void)?
    private var tokenTask: Task<Void, Never>?

    init() {
        if let existing = LiveActivitySink.visible.first {
            log("Live Activity adopted from a previous run")
            observeToken(existing)
        }
    }

    var current: Activity<LensActivityAttributes>? { LiveActivitySink.visible.first }

    var stateDescription: String {
        guard let activity = Activity<LensActivityAttributes>.activities.last else { return "none" }
        switch activity.activityState {
        case .active: return "active"
        case .ended: return "ended"
        case .dismissed: return "dismissed"
        case .stale: return "stale"
        case .pending: return "pending"
        @unknown default: return "unknown"
        }
    }

    var isRunning: Bool { current != nil }

    var enabled: Bool { ActivityAuthorizationInfo().areActivitiesEnabled }

    /// Ends every old activity, then starts a new one. Asks for a push token when the build can get one.
    func start(state: LensActivityAttributes.ContentState, wantPush: Bool) async {
        guard enabled else {
            startError = "Live Activities are off for \(LensBrand.name) (Settings > \(LensBrand.name) > Live Activities)"
            log(startError)
            return
        }
        for old in Activity<LensActivityAttributes>.activities {
            await old.end(nil, dismissalPolicy: .immediate)
        }
        sink.reset()
        pushToken = nil
        let content = ActivityContent(state: state, staleDate: nil)
        let attributes = LensActivityAttributes(title: LensBrand.name)
        do {
            let activity: Activity<LensActivityAttributes>
            if wantPush {
                do {
                    activity = try Activity.request(attributes: attributes, content: content, pushType: .token)
                } catch {
                    log("Live Activity with push token failed (\(error.localizedDescription)), starting without push")
                    activity = try Activity.request(attributes: attributes, content: content, pushType: nil)
                }
            } else {
                activity = try Activity.request(attributes: attributes, content: content, pushType: nil)
            }
            startError = ""
            log("Live Activity started\(wantPush ? " (push token requested)" : "")")
            observeToken(activity)
        } catch {
            startError = error.localizedDescription
            log("Live Activity start error: \(error.localizedDescription)")
        }
    }

    private func observeToken(_ activity: Activity<LensActivityAttributes>) {
        tokenTask?.cancel()
        // An adopted activity (after a relaunch) has its token already; the server may have lost it.
        if let data = activity.pushToken { pushToken = data.map { String(format: "%02x", $0) }.joined() }
        tokenTask = Task { [weak self] in
            for await data in activity.pushTokenUpdates {
                let token = data.map { String(format: "%02x", $0) }.joined()
                self?.pushToken = token
                log("Live Activity push token \(token.prefix(8))...")
                self?.onPushToken?(token)
            }
        }
    }

    /// Returns true when an update was sent.
    @discardableResult
    func update(_ state: LensActivityAttributes.ContentState, alerts: Bool, sound: Bool, force: Bool = false) -> Bool {
        let background = UIApplication.shared.applicationState != .active
        let sent = sink.apply(state, alerts: alerts, sound: sound, forceAlert: force) { line in
            Task { @MainActor in log(line + (background ? " (app in background)" : "")) }
        }
        if sent && background { updatesInBackground += 1 }
        if sent {
            lastUpdateAt = Date()
            lastUpdateMode = state.mode.rawValue + (state.handle.map { " " + $0 } ?? "")
        }
        return sent
    }

    func end() {
        let all = Activity<LensActivityAttributes>.activities
        tokenTask?.cancel()
        Task {
            for a in all { await a.end(nil, dismissalPolicy: .immediate) }
            log("Live Activity ended (\(all.count))")
        }
        sink.reset()
        pushToken = nil
    }
}

/// Reads `aps-environment` from the embedded provisioning profile. None means the build cannot get push tokens
/// (simulator, or a build without the push entitlement). Phone builds are development-signed: "development".
enum ProvisioningInfo {
    static let apsEnvironment: String? = {
        #if targetEnvironment(simulator)
        // A simulator build has no profile, but a simulator on an Apple silicon Mac gets sandbox push tokens.
        return "development"
        #else
        guard let url = Bundle.main.url(forResource: "embedded", withExtension: "mobileprovision"),
              let data = try? Data(contentsOf: url),
              let text = String(data: data, encoding: .isoLatin1),
              let start = text.range(of: "<?xml"),
              let end = text.range(of: "</plist>") else { return nil }
        let plist = Data(text[start.lowerBound..<end.upperBound].utf8)
        guard let dict = try? PropertyListSerialization.propertyList(from: plist, format: nil) as? [String: Any],
              let entitlements = dict["Entitlements"] as? [String: Any] else { return nil }
        return entitlements["aps-environment"] as? String
        #endif
    }()
}
