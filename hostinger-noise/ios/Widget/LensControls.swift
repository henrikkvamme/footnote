import ActivityKit
import AppIntents
import SwiftUI
import WidgetKit

/// "Footnote this" as a Control (iOS 18): it can go on the Action button, in Control Center and on the Lock Screen.
/// The intent runs in the app process (LiveActivityIntent), so the app does not open.
struct FootnoteNowControl: ControlWidget {
    static let kind = "no.henrikkvamme.lens.control.footnote-now"

    var body: some ControlWidgetConfiguration {
        StaticControlConfiguration(kind: Self.kind) {
            ControlWidgetButton(action: FootnoteNowIntent()) {
                Label(LensBrand.Words.footnoteThis, systemImage: "asterisk")
            }
        }
        .displayName("Footnote this")
        .description("Shows the check of the last claim in the Dynamic Island now.")
    }
}

/// "New take" as a Control: clears the claims and checks of this session and the Island, without opening the app.
struct NewTakeControl: ControlWidget {
    static let kind = "no.henrikkvamme.lens.control.new-take"

    var body: some ControlWidgetConfiguration {
        StaticControlConfiguration(kind: Self.kind) {
            ControlWidgetButton(action: NewTakeIntent()) {
                Label("New take", systemImage: "arrow.counterclockwise")
            }
        }
        .displayName("Footnote new take")
        .description("Clears the claims and checks of this session. The broadcast keeps running.")
    }
}

/// Start or stop listening: the Live Activity and the keep-alive. The broadcast itself needs the iOS sheet in the app.
struct ListeningControl: ControlWidget {
    static let kind = ListeningControlKind.kind

    var body: some ControlWidgetConfiguration {
        StaticControlConfiguration(kind: Self.kind, provider: ListeningValueProvider()) { isOn in
            ControlWidgetToggle("Footnote", isOn: isOn, action: ToggleListeningIntent()) { on in
                Label(on ? "Listening" : "Not listening", systemImage: on ? "asterisk.circle.fill" : "asterisk.circle")
            }
        }
        .displayName("Footnote listening")
        .description("Starts or stops listening. Start the broadcast once in Footnote.")
    }
}

/// The widget extension cannot read the app's settings (no App Group), but it sees the app's Live Activity:
/// listening is on while the activity runs.
struct ListeningValueProvider: ControlValueProvider {
    var previewValue: Bool { false }

    func currentValue() async throws -> Bool {
        Activity<LensActivityAttributes>.activities.contains { $0.activityState == .active || $0.activityState == .stale }
    }
}
