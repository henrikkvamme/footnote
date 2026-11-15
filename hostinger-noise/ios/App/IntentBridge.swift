import AppIntents
import Foundation
import UIKit
import WidgetKit

/// The app side of the intents in Shared/FootnoteIntents.swift. iOS launches the app in the background for them
/// when it does not run, so they use the one shared AppModel.
@MainActor
enum IntentBridge {
    static func footnoteNow() async {
        logProcess("footnote this")
        await AppModel.shared.footnoteNow()
    }

    static func newTake() async {
        logProcess("new take")
        await AppModel.shared.newTake()
    }

    /// Where an intent runs: the process and whether the app was in front (the log on /debug shows it).
    private static func logProcess(_ name: String) {
        let phase = UIApplication.shared.applicationState == .active ? "in front" : "in the background"
        log("intent \(name) runs in \(ProcessInfo.processInfo.processName), app \(phase)")
    }

    static func setListening(_ on: Bool) async {
        let model = AppModel.shared
        if on, !model.listening {
            await model.startListeningInBackground()
        } else if !on, model.listening {
            model.stopListening()
        }
        ControlCenter.shared.reloadControls(ofKind: ListeningControlKind.kind)
    }
}

/// Shows the intents in the Shortcuts app and in Settings > Action Button > Shortcut without any setup.
struct FootnoteShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: FootnoteNowIntent(),
                    phrases: ["Footnote this in \(.applicationName)", "\(.applicationName) this", "Check this with \(.applicationName)"],
                    shortTitle: "Footnote this", systemImageName: "asterisk")
        AppShortcut(intent: NewTakeIntent(),
                    phrases: ["New take in \(.applicationName)", "Reset \(.applicationName)"],
                    shortTitle: "New take", systemImageName: "arrow.counterclockwise")
        AppShortcut(intent: StartListeningIntent(),
                    phrases: ["Start listening in \(.applicationName)", "Start \(.applicationName)"],
                    shortTitle: "Start listening", systemImageName: "ear")
        AppShortcut(intent: StopListeningIntent(),
                    phrases: ["Stop listening in \(.applicationName)", "Stop \(.applicationName)"],
                    shortTitle: "Stop listening", systemImageName: "stop.circle")
    }

    static var shortcutTileColor: ShortcutTileColor { .yellow }
}

/// POST /api/now {session}: the server judges the last seconds now, with a lower bar, and answers with one result
/// (`forced: true`) or `{result: null, reason}`. Before the server has the endpoint (404), the app falls back to the
/// newest results it already has, and says so in the log.
enum NowClient {
    struct Response: Decodable {
        let result: TranscriptResult?
        let reason: String?
    }

    enum Outcome {
        case result(TranscriptResult, fallback: Bool)
        /// The server checked a claim (or started to): kind "check", forced.
        case check(CheckResult)
        case nothing(reason: String, line: String?, fallback: Bool)
        case failed(String)
    }

    private static let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        // No answer in 8 s: the logo turns its two steps and nothing else.
        config.timeoutIntervalForRequest = 8
        config.timeoutIntervalForResource = 8
        config.waitsForConnectivity = false
        return URLSession(configuration: config)
    }()

    static func ask(lastResultId: Int) async -> Outcome {
        var request = URLRequest(url: LensConfig.serverURL.appendingPathComponent("api/now"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: ["session": LensConfig.session])
        do {
            let (data, response) = try await session.data(for: request)
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            if status == 404 || status == 405 || status == 501 {
                return await fallback(lastResultId: lastResultId)
            }
            guard status == 200 else { return .failed("server answered \(status)") }
            return try decode(data)
        } catch {
            return .failed(error.localizedDescription)
        }
    }

    /// The server answers with the result itself (`forced: true`): a note, an unsure line, a result with
    /// `noNote: true`, or `{text: "Paused: private screen", note: false}` on a private screen. The first contract,
    /// `{result, reason}`, is read too.
    static func decode(_ data: Data) throws -> Outcome {
        let object = (try JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        if object["kind"] as? String == "check" {
            var c = try JSONDecoder().decode(CheckResult.self, from: data)
            c.forced = true
            return .check(c)
        }
        if let inner = object["result"] as? [String: Any], inner["kind"] as? String == "check" {
            var c = try JSONDecoder().decode(CheckResult.self, from: JSONSerialization.data(withJSONObject: inner))
            c.forced = true
            return .check(c)
        }
        if object.keys.contains("result") || object.keys.contains("reason") {
            let decoded = try JSONDecoder().decode(Response.self, from: data)
            if let result = decoded.result { return .result(result, fallback: false) }
            return .nothing(reason: decoded.reason ?? "nothing", line: nil, fallback: false)
        }
        if object["note"] as? Bool == false {
            let text = object["text"] as? String ?? ""
            return .nothing(reason: text.lowercased().hasPrefix("paused") ? "paused" : "nothing", line: nil, fallback: false)
        }
        if object["noNote"] as? Bool == true || object["id"] == nil {
            return .nothing(reason: "nothing", line: nil, fallback: false)
        }
        let result = try JSONDecoder().decode(TranscriptResult.self, from: data)
        if result.technique == nil, result.unsure != true {
            return .nothing(reason: "nothing", line: nil, fallback: false)
        }
        return .result(result, fallback: false)
    }

    /// The newest marked line among the last few results, else the newest line with "No note".
    private static func fallback(lastResultId: Int) async -> Outcome {
        do {
            let url = LensAPI.latestURL(since: max(0, lastResultId - 12), source: "now")
            let (data, _) = try await session.data(from: url)
            let latest = try LensAPI.decode(data)
            let recent = latest.results.suffix(4)
            if let marked = recent.last(where: { $0.technique != nil || $0.unsure == true }) {
                return .result(marked, fallback: true)
            }
            let line = recent.last(where: \.speech)?.text
            return .nothing(reason: line == nil ? "silence" : "nothing", line: line, fallback: true)
        } catch {
            return .failed(error.localizedDescription)
        }
    }
}
