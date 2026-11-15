import AppIntents
import SwiftUI

/// The Live Activity views, shared by the widget extension (the real Island and Lock Screen) and the app
/// (a preview). The style is the Footnote brand (LensBrand): a marked sentence gets an asterisk, and the note
/// goes below a short rule, as in a printed book. No counters, no warning symbols, no "found".

typealias LensState = LensActivityAttributes.ContentState

extension LensActivityAttributes.ContentState {
    /// A sentence was marked a moment ago, and the state has the sentence.
    var marked: Bool { mode == .found && hasFlag }

    var hasFlag: Bool { flagText != nil && !(flagText ?? "").isEmpty }

    /// "@name" for the creator line, with the app when it is known ("@name, YouTube"); nil when the screen gave neither.
    var handleLine: String? {
        let app = LensBrand.appName(self.app)
        guard let handle, !handle.isEmpty else { return app }
        let name = handle.hasPrefix("@") || handle.contains(" ") ? handle : "@" + handle
        return app.map { "\(name), \($0)" } ?? name
    }

    /// The Island and the Lock Screen offer "Footnote this" while there is something to read.
    var offersFootnote: Bool { mode == .listening || mode == .nothing || mode == .found }

    /// Where a tap on the activity goes: the start step when the broadcast must be started, else the page.
    var tapURL: URL {
        // A check opens its claim with the sources; without an id, the newest checked claim on the page.
        if mode == .checked { return URL(string: "footnote://check/\(noteId ?? 0)")! }
        return URL(string: mode == .waiting || mode == .idle ? "footnote://start" : "footnote://open")!
    }

    /// A technique note that should not draw the eye: checks are the hero.
    var quiet: Bool { quietNotes == true }

    /// "2 notes in this video" (with screen reading) or "2 notes"; nil when there are none.
    var notesLine: String? {
        let n = videoNotes ?? count
        guard n > 0 else { return nil }
        return LensBrand.notes(n) + (videoNotes != nil ? " in this video" : "")
    }

    /// The quiet meta text of a state: what the Lock Screen header says on the right.
    var metaLine: String {
        switch mode {
        case .idle: return LensBrand.Words.notListening
        case .waiting: return LensBrand.Words.notListening
        case .paused: return LensBrand.Words.paused
        case .reading: return LensBrand.Words.reading
        case .checked: return LensBrand.Words.factCheck
        case .checking: return LensBrand.Words.listening
        case .listening, .found, .nothing: return (quiet ? nil : notesLine) ?? LensBrand.Words.listening
        }
    }
}

// MARK: Pieces

/// The marked sentence and its asterisk. On black: a thin Marker underline. On paper: a Marker stroke behind the
/// lower half of the text. The whole sentence is marked, not single words.
struct FootnoteSentence: View {
    let text: String
    let onBlack: Bool
    var size: CGFloat = 17

    var body: some View {
        if onBlack {
            Text("\(Text(text).underline(true, color: LensBrand.marker))\(Text("*").font(LensBrand.Serif.bold(size)).foregroundStyle(LensBrand.marker))")
                .font(LensBrand.Serif.regular(size))
                .foregroundStyle(LensBrand.cream)
                .lineSpacing(2)
        } else {
            Text("\(Text(text).customAttribute(FootnoteMarked()))\(Text("*").font(LensBrand.Serif.bold(size)))")
                .font(LensBrand.Serif.regular(size))
                .foregroundStyle(LensBrand.ink)
                .lineSpacing(3)
                .textRenderer(MarkerStrokeRenderer())
        }
    }
}

/// "* Name. Reason." The name in bold, the reason in soft ink. No reason: the name only.
struct FootnoteNote: View {
    let label: String
    let why: String?
    let onBlack: Bool
    var size: CGFloat = 14
    /// A number for the page; nil gives the asterisk.
    var number: Int? = nil

    var body: some View {
        let main = onBlack ? LensBrand.cream : LensBrand.ink
        let soft = onBlack ? LensBrand.cream.opacity(0.7) : LensBrand.softInk
        let star = Text(number.map { "\($0)" } ?? "*")
            .font(number == nil ? LensBrand.Serif.bold(size) : .system(size: size * 0.72, weight: .semibold))
            .foregroundStyle(number == nil && onBlack ? LensBrand.marker : main)
        let name = Text(label + ".").font(LensBrand.Serif.bold(size)).foregroundStyle(main)
        let reason = Text(why.map { " " + $0 } ?? "").font(LensBrand.Serif.regular(size)).foregroundStyle(soft)
        return Text("\(star) \(name)\(reason)")
            .lineSpacing(1)
    }
}

/// A check that came back later: where the claim was heard, the claim with its asterisk, the rule, "* Checked." with
/// what the sources say, and the first source. Ink, cream and Marker only: no verdict colours, no "true" or "false".
struct CheckedNote: View {
    let state: LensState
    let onBlack: Bool

    // The expanded Island has room for about four lines (measured in the iOS 26.5 simulator). There the claim has
    // one line, the line on the sources two, and the video and the source share the last row. The Lock Screen has
    // room for the full layout with the rule.
    var body: some View {
        let soft = onBlack ? LensBrand.softCream : LensBrand.softInk
        VStack(alignment: .leading, spacing: onBlack ? 4 : 8) {
            if !onBlack, let origin = state.checkOf {
                meta(origin, soft)
            }
            if let claim = state.flagText, !claim.isEmpty {
                // The claim is the marked sentence: a thin Marker underline on black, the Marker stroke on paper.
                if onBlack {
                    FootnoteSentence(text: claim, onBlack: true, size: 16)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    FootnoteSentence(text: claim, onBlack: false, size: 17)
                        .lineLimit(2)
                }
            }
            if !onBlack {
                FootnoteRule(color: LensBrand.rule)
            }
            FootnoteNote(label: state.flagLabel ?? LensBrand.Words.checked, why: state.checkLine, onBlack: onBlack, size: onBlack ? 13.5 : 14)
                .lineLimit(onBlack ? 2 : 3)
                .fixedSize(horizontal: false, vertical: true)
                .layoutPriority(1)
            if onBlack {
                HStack(spacing: 8) {
                    if let origin = state.checkOf { meta(origin, soft) }
                    Spacer(minLength: 0)
                    if let source = state.checkSource { meta(source, soft).layoutPriority(-1) }
                }
                // The bottom corners of the Island curve in: keep the last row off them.
                .padding(.horizontal, 6)
            } else if let source = state.checkSource {
                meta(source, soft)
            }
        }
    }

    private func meta(_ text: String, _ color: Color) -> some View {
        Text(text)
            .font(.system(size: 11, weight: .medium))
            .foregroundStyle(color)
            .lineLimit(1)
            .truncationMode(.tail)
    }
}

/// The creator of the video in front: small, quiet meta text.
struct FootnoteHandle: View {
    let state: LensState
    let color: Color

    var body: some View {
        if let line = state.handleLine {
            Text(line)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(color)
                .lineLimit(1)
                .truncationMode(.tail)
        }
    }
}

// MARK: Dynamic Island

/// Compact leading and minimal: the asterisk. Dim while it listens, Marker when a sentence is marked.
struct LensCompactLeading: View {
    let state: LensState

    var body: some View {
        if state.mode == .checking {
            CheckingAsterisk(state: state)
        } else {
            Image(systemName: "asterisk")
                .font(.system(size: 13, weight: .heavy))
                .foregroundStyle(lit ? LensBrand.marker : LensBrand.softCream)
                .frame(width: 18, height: 18)
                .accessibilityLabel(state.mode == .checked ? state.flagLabel ?? LensBrand.Words.checked : lit ? "New note" : LensBrand.Words.listening)
        }
    }

    /// Marker for a check that came back, and for a technique note unless notes are quiet.
    private var lit: Bool {
        state.mode == .checked || (state.mode == .found && !state.quiet)
    }
}

/// While a check runs, the asterisk is the only sign: steady Marker with a soft glow, and it turns one step at a time.
/// No words in the Island.
struct CheckingAsterisk: View {
    let state: LensState
    var size: CGFloat = 13
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        // Each update that counts `turn` up starts one clockwise turn of the whole symbol, as a symbol effect: a
        // Live Activity on the phone animates symbol effects, not a changed rotation angle. No scale, no blink.
        // Reduce Motion: it stays still and lit.
        let turn = reduceMotion ? 0 : (state.turn ?? 0)
        Image(systemName: "asterisk")
            .font(.system(size: size, weight: .heavy))
            .foregroundStyle(LensBrand.marker)
            .symbolEffect(.rotate.clockwise.wholeSymbol, options: .speed(0.7), value: turn)
            .shadow(color: LensBrand.marker.opacity(0.5), radius: 4)
            .frame(width: size + 5, height: size + 5)
            .accessibilityLabel(LensBrand.Words.checking)
    }
}

/// The minimal Island, when another activity (such as the screen broadcast) takes the compact one: a dim asterisk,
/// the lit asterisk that turns while a claim is under check, and for a result the asterisk on a marker disc.
struct LensMinimal: View {
    let state: LensState

    var body: some View {
        switch state.mode {
        case .checking:
            CheckingAsterisk(state: state, size: 15)
        case .checked:
            Image(systemName: "asterisk")
                .font(.system(size: 12, weight: .heavy))
                .foregroundStyle(Color.black)
                .frame(width: 22, height: 22)
                .background(LensBrand.marker, in: Circle())
                .accessibilityLabel(state.flagLabel ?? LensBrand.Words.checked)
        default:
            LensCompactLeading(state: state)
        }
    }
}

/// Compact trailing: empty while it listens, the short technique name in Marker when marked.
struct LensCompactTrailing: View {
    let state: LensState

    var body: some View {
        Group {
            if state.mode == .found, let short = state.flagShort {
                Text(short)
                    .font(.system(size: state.quiet ? 13 : 14, weight: state.quiet ? .medium : .semibold))
                    .foregroundStyle(state.quiet ? LensBrand.softCream : LensBrand.marker)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                    .frame(maxWidth: 70, alignment: .trailing)
            } else if state.mode == .checked {
                Text(state.flagShort ?? LensBrand.Words.checked)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(LensBrand.marker)
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)
                    .frame(maxWidth: 118, alignment: .trailing)
            } else if state.mode == .reading {
                ProgressView()
                    .controlSize(.mini)
                    .tint(LensBrand.cream)
            } else if state.mode == .nothing {
                Text(state.flagLabel == LensBrand.Words.notSure ? "Not sure" : "No note")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(LensBrand.softCream)
                    .lineLimit(1)
            } else if state.mode == .waiting {
                Text("Start")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(LensBrand.softCream)
                    .lineLimit(1)
            } else if state.mode == .paused {
                Text("Paused")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(LensBrand.softCream)
                    .lineLimit(1)
            } else {
                Color.clear.frame(width: 4, height: 4)
            }
        }
        .padding(.trailing, 2)
    }
}

/// Expanded: only the sentence, the rule and the footnote. No header, no logo, no counters. While there is something
/// to read, one quiet "Footnote this" button sits under it.
struct LensExpandedBody: View {
    let state: LensState

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            switch state.mode {
            case .found where state.hasFlag:
                FootnoteSentence(text: state.flagText ?? "", onBlack: true, size: 18)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                FootnoteRule(color: LensBrand.creamRule)
                FootnoteNote(label: state.flagLabel ?? "Note", why: state.flagWhy, onBlack: true)
                    .lineLimit(2)
            case .nothing:
                if let text = state.flagText, !text.isEmpty {
                    Text(text)
                        .font(LensBrand.Serif.regular(18))
                        .foregroundStyle(LensBrand.cream)
                        .lineSpacing(2)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                    FootnoteRule(color: LensBrand.creamRule)
                }
                FootnoteNote(label: String((state.flagLabel ?? LensBrand.Words.noNote).dropLast()), why: state.flagWhy, onBlack: true)
                    .lineLimit(2)
            case .checked:
                CheckedNote(state: state, onBlack: true)
            case .checking:
                // No words: only the lit asterisk that turns (a pushed state has no line).
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    CheckingAsterisk(state: state, size: 17)
                    if !state.line.isEmpty {
                        Text(state.line)
                            .font(LensBrand.Serif.regular(16))
                            .foregroundStyle(LensBrand.softCream)
                            .lineLimit(2)
                    }
                }
            case .reading:
                Text(LensBrand.Words.reading)
                    .font(LensBrand.Serif.italic(17))
                    .foregroundStyle(LensBrand.cream.opacity(0.8))
                if !state.line.isEmpty {
                    Text(state.line)
                        .font(LensBrand.Serif.regular(15))
                        .foregroundStyle(LensBrand.softCream)
                        .lineLimit(2)
                }
            case .paused:
                Text(LensBrand.Words.paused)
                    .font(LensBrand.Serif.regular(17))
                    .foregroundStyle(LensBrand.cream.opacity(0.7))
                Text(LensBrand.Words.pausedHint)
                    .font(.system(size: 12))
                    .foregroundStyle(LensBrand.softCream)
                    .lineLimit(2)
            case .waiting:
                Text(LensBrand.Words.startBroadcast)
                    .font(LensBrand.Serif.regular(17))
                    .foregroundStyle(LensBrand.cream)
                Text(LensBrand.Words.startBroadcastHint)
                    .font(.system(size: 12))
                    .foregroundStyle(LensBrand.softCream)
                    .lineLimit(2)
            case .idle:
                Text(LensBrand.Words.notListening)
                    .font(LensBrand.Serif.regular(17))
                    .foregroundStyle(LensBrand.cream.opacity(0.7))
                Text(LensBrand.Words.startHint)
                    .font(.system(size: 12))
                    .foregroundStyle(LensBrand.softCream)
                    .lineLimit(1)
            case .listening where state.line.isEmpty:
                // Footnote checks facts: no running captions in the Island, the promise in two lines. A state the
                // server pushes has no quietNotes and no line: it gets the promise too.
                Text(LensBrand.Words.lockListening)
                    .font(LensBrand.Serif.regular(15))
                    .foregroundStyle(LensBrand.softCream)
                    .lineSpacing(2)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(alignment: .center, spacing: 8) {
                    FootnoteHandle(state: state, color: LensBrand.softCream)
                    Spacer(minLength: 0)
                    FootnoteThisButton(onBlack: true)
                }
            default:
                Text(state.line.isEmpty ? LensBrand.Words.waiting : state.line)
                    .font(LensBrand.Serif.regular(17))
                    .foregroundStyle(state.line.isEmpty ? LensBrand.softCream : LensBrand.cream)
                    .lineSpacing(2)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                // The creator on the left, "Footnote this" on the right: one row, so the Island stays short.
                HStack(alignment: .center, spacing: 8) {
                    FootnoteHandle(state: state, color: LensBrand.softCream)
                    Spacer(minLength: 0)
                    FootnoteThisButton(onBlack: true)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// "* Footnote this": runs FootnoteNowIntent in the app process without opening the app.
struct FootnoteThisButton: View {
    let onBlack: Bool

    var body: some View {
        Button(intent: FootnoteNowIntent()) {
            HStack(spacing: 5) {
                Text("*").font(LensBrand.Serif.bold(17)).offset(y: 3)
                Text(LensBrand.Words.footnoteThis).font(.system(size: 13, weight: .semibold))
            }
            .foregroundStyle(onBlack ? LensBrand.ink : LensBrand.cream)
            .padding(.horizontal, 12)
            .frame(height: 30)
            .background(onBlack ? LensBrand.marker : LensBrand.ink, in: Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(LensBrand.Words.footnoteThis)
    }
}

// MARK: Lock Screen

/// Paper, ink text. One quiet header row, then the sentence, the rule and the footnote.
struct LensLockScreenView: View {
    let state: LensState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text(LensBrand.name)
                    .font(LensBrand.Serif.regular(16))
                    .foregroundStyle(LensBrand.ink)
                if state.mode == .checking {
                    CheckingAsterisk(state: state, size: 12)
                        .offset(x: -6, y: -6)
                }
                Spacer(minLength: 8)
                Text(state.metaLine)
                    .font(.system(size: 12))
                    .foregroundStyle(LensBrand.softInk)
                    .lineLimit(1)
            }

            switch state.mode {
            case .found where state.hasFlag:
                FootnoteSentence(text: state.flagText ?? "", onBlack: false, size: 18)
                    .lineLimit(3)
                FootnoteRule(color: LensBrand.rule)
                FootnoteNote(label: state.flagLabel ?? "Note", why: state.flagWhy, onBlack: false)
                    .lineLimit(2)
            case .nothing:
                if let text = state.flagText, !text.isEmpty {
                    Text(text)
                        .font(LensBrand.Serif.regular(18))
                        .foregroundStyle(LensBrand.ink)
                        .lineLimit(3)
                    FootnoteRule(color: LensBrand.rule)
                }
                FootnoteNote(label: String((state.flagLabel ?? LensBrand.Words.noNote).dropLast()), why: state.flagWhy, onBlack: false)
                    .lineLimit(2)
            case .checked:
                CheckedNote(state: state, onBlack: false)
            case .checking:
                // No words on the check: the lit asterisk in the header is the only sign. The promise stays, so the
                // card keeps its height.
                Text(LensBrand.Words.lockListening)
                    .font(LensBrand.Serif.regular(17))
                    .foregroundStyle(LensBrand.softInk)
                    .lineSpacing(2)
                    .lineLimit(2)
            case .reading:
                Text(LensBrand.Words.reading)
                    .font(LensBrand.Serif.italic(17))
                    .foregroundStyle(LensBrand.softInk)
            case .paused:
                Text(LensBrand.Words.pausedHint)
                    .font(LensBrand.Serif.regular(16))
                    .foregroundStyle(LensBrand.softInk)
                    .lineLimit(2)
            case .waiting:
                Text(LensBrand.Words.startBroadcast)
                    .font(LensBrand.Serif.regular(17))
                    .foregroundStyle(LensBrand.ink)
                Text(LensBrand.Words.startBroadcastHint)
                    .font(.system(size: 13))
                    .foregroundStyle(LensBrand.softInk)
                    .lineLimit(2)
            case .idle:
                Text(LensBrand.Words.startHint)
                    .font(LensBrand.Serif.regular(16))
                    .foregroundStyle(LensBrand.softInk)
                    .lineLimit(2)
            case .listening where state.quiet || state.line.isEmpty:
                // Footnote checks facts: no running captions, the promise in one line (also for a pushed state).
                Text(LensBrand.Words.lockListening)
                    .font(LensBrand.Serif.regular(17))
                    .foregroundStyle(LensBrand.softInk)
                    .lineSpacing(2)
                    .lineLimit(2)
            default:
                Text(state.line.isEmpty ? LensBrand.Words.waiting : state.line)
                    .font(LensBrand.Serif.regular(17))
                    .foregroundStyle(state.line.isEmpty ? LensBrand.softInk : LensBrand.ink)
                    .lineSpacing(2)
                    .lineLimit(3)
            }
            if state.mode == .listening, !state.quiet {
                FootnoteHandle(state: state, color: LensBrand.softInk)
            }
            if state.offersFootnote {
                FootnoteThisButton(onBlack: false)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 18)
        .padding(.vertical, 16)
    }
}
