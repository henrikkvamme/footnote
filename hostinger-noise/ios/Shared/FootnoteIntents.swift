import AppIntents
import Foundation

/// The intents of the Action button, the Controls, the Shortcuts app and the Island button.
/// Compiled into the app and the widget extension. They are Live Activity intents, so iOS runs them in the app
/// process (in the background, without opening the app) and they may start or update the Live Activity there.
/// The work is in the app (IntentBridge, only with LENS_APP). Should iOS run one in the widget extension, it posts a
/// Darwin signal there, and the app (kept alive by its silent audio while listening) does the work.

enum ListeningControlKind {
    static let kind = "no.henrikkvamme.lens.control.listening"
}

/// "Footnote this": the check of the last claim, now, in the Dynamic Island.
struct FootnoteNowIntent: LiveActivityIntent {
    static let title: LocalizedStringResource = "Footnote this"
    static let description = IntentDescription("Shows the check of the last claim you heard in the Dynamic Island now, with its sources.")
    static let openAppWhenRun = false

    init() {}

    func perform() async throws -> some IntentResult {
        #if LENS_APP
        await IntentBridge.footnoteNow()
        #else
        BroadcastSignal.footnoteNowRequest.post()
        #endif
        return .result()
    }
}

/// "New take": the server and the app forget this session's claims and checks, and the Island goes back to the dim
/// listening state, so a recording can start clean without stopping the broadcast.
struct NewTakeIntent: LiveActivityIntent {
    static let title: LocalizedStringResource = "New take"
    static let description = IntentDescription("Clears the claims and checks of this session and the Dynamic Island. The broadcast keeps running.")
    static let openAppWhenRun = false

    init() {}

    func perform() async throws -> some IntentResult {
        #if LENS_APP
        await IntentBridge.newTake()
        #else
        BroadcastSignal.newTakeRequest.post()
        #endif
        return .result()
    }
}

/// Starts the Live Activity and the keep-alive. The broadcast needs the iOS sheet: when it does not run, the Island
/// says so and a tap opens the app at the start step.
struct StartListeningIntent: LiveActivityIntent {
    static let title: LocalizedStringResource = "Start listening"
    static let description = IntentDescription("Starts Footnote in the Dynamic Island. Start the broadcast once in Footnote.")
    static let openAppWhenRun = false

    init() {}

    func perform() async throws -> some IntentResult {
        #if LENS_APP
        await IntentBridge.setListening(true)
        #endif
        return .result()
    }
}

struct StopListeningIntent: LiveActivityIntent {
    static let title: LocalizedStringResource = "Stop listening"
    static let description = IntentDescription("Ends Footnote in the Dynamic Island.")
    static let openAppWhenRun = false

    init() {}

    func perform() async throws -> some IntentResult {
        #if LENS_APP
        await IntentBridge.setListening(false)
        #endif
        return .result()
    }
}

/// The Control Center toggle.
struct ToggleListeningIntent: SetValueIntent, LiveActivityIntent {
    static let title: LocalizedStringResource = "Footnote listening"
    static let openAppWhenRun = false

    @Parameter(title: "Listening")
    var value: Bool

    init() {}

    func perform() async throws -> some IntentResult {
        #if LENS_APP
        await IntentBridge.setListening(value)
        #endif
        return .result()
    }
}
