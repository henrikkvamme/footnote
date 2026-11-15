import ActivityKit
import Foundation

/// The Live Activity data. Compiled into the app, the broadcast extension and the widget extension.
/// The server builds the same JSON (`live_state` in server/server.py) for /api/latest and for APNs pushes,
/// so keep the field names in step with it.
struct LensActivityAttributes: ActivityAttributes {
    /// While a check runs, the app sends one turn step this often.
    static let stepSeconds = 1.0
    /// One turn step: one arm spacing of the six-arm asterisk, so the shape is the same after each step.
    static let stepDegrees = 360.0 / 6

    struct ContentState: Codable, Hashable {
        enum Mode: String, Codable, Hashable {
            /// The broadcast runs and nothing was found in the last seconds.
            case listening
            /// A sentence got a note a moment ago.
            case found
            /// No broadcast yet, or it stopped.
            case idle
            /// A private screen is in front (or, in TikTok-only mode, another app): the server keeps nothing.
            case paused
            /// "Footnote this" was pressed: the server checks the last seconds now.
            case reading
            /// "Footnote this" found nothing to note (or was not sure): the line and an honest "No note."
            case nothing
            /// Listening is on, but the broadcast does not run: the Island asks to start it in the app.
            case waiting
            /// A fact-check came back: `flagText` is the claim, `flagLabel` and `flagShort` the stance in words
            /// ("Sources differ"). Shown only when the server sets notify, or for "Footnote this".
            case checked
            /// "Footnote this" was pressed with checks first: "Checking a claim..." until the check comes back.
            case checking
        }

        var mode: Mode
        /// The newest transcript line, cut at the start so the newest words stay.
        var line: String
        /// Techniques found in this run.
        var count: Int
        /// Transcribed chunks in this run.
        var chunks: Int
        /// The last flagged sentence, cut around the marked part.
        var flagText: String?
        /// The exact part of `flagText` to mark.
        var flagMark: String?
        /// The technique in plain words, for example "Appeal to authority".
        var flagLabel: String?
        /// A short name that fits the compact Island, for example "Authority".
        var flagShort: String?
        /// One line on why it is a technique.
        var flagWhy: String?
        /// Time of the newest result (stable, so equal states compare equal).
        var updatedAt: Date
        /// The creator of the video in front, read from the screen, so the viewer sees which video the line belongs to.
        var handle: String? = nil
        /// Notes on the video in front (the screen tells when a new video starts). Nil without screen reading.
        var videoNotes: Int? = nil
        /// The app in front, as the server names it ("tiktok", "youtube", "news"). Nil when unknown.
        var app: String? = nil
        /// A check: one line on what the sources say (at most 90 characters), never "true" or "false".
        var checkLine: String? = nil
        /// A check: the first source, such as "Reuters, 12 Nov 2026".
        var checkSource: String? = nil
        /// A check: which video the claim is from, such as "From an earlier video: @name, TikTok".
        var checkOf: String? = nil
        /// A check: the server result id of the claim, so a tap opens the app at that note.
        var noteId: Int? = nil
        /// Technique notes do not expand the Island (checks first, or expansion off): their compact name is quiet.
        var quietNotes: Bool? = nil
        /// While a check runs, the app counts this up by one about each second (it never goes down), and the
        /// asterisk turns to `turn` times one arm spacing: each update turns it one step forward, then it rests.
        /// A Live Activity runs no repeating animation of its own.
        var turn: Int? = nil

        static func idle(at date: Date = .now) -> ContentState {
            ContentState(mode: .idle, line: "", count: 0, chunks: 0, updatedAt: date)
        }
    }

    var title: String
}
