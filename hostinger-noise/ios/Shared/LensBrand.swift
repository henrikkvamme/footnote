import SwiftUI

/// The Footnote brand in one place: name, colours, type and words (hostinger-noise/brand/README.md).
/// Compiled into the app and the widget extension. The code names (Lens*) stay; only the visible brand is here.
///
/// Rule from the brand: very clean, minimal, no noise. One accent colour (Marker), on a mark and an asterisk only.
enum LensBrand {
    static let name = "Footnote"
    static let line = "Captions with footnotes."

    /// Background of the app, the Lock Screen activity and the card.
    static let paper = Color(red: 0xF3 / 255, green: 0xEE / 255, blue: 0xE2 / 255)
    /// Text on paper.
    static let ink = Color(red: 0x21 / 255, green: 0x1E / 255, blue: 0x18 / 255)
    /// Earlier speech, the reason, meta text.
    static let softInk = ink.opacity(0.55)
    /// The short rule above a footnote, on paper.
    static let rule = ink.opacity(0.5)
    /// The mark on a sentence and the asterisk on black. Nothing else.
    static let marker = Color(red: 0xFF / 255, green: 0xE4 / 255, blue: 0x5C / 255)
    /// Text on black (the Dynamic Island).
    static let cream = paper
    static let softCream = paper.opacity(0.42)
    static let creamRule = paper.opacity(0.4)

    /// Iowan Old Style ships with iOS: the speech and the footnote. SF Pro only for small meta text.
    /// Sizes scale with Dynamic Type (relative to `style`).
    enum Serif {
        static func regular(_ size: CGFloat, _ style: Font.TextStyle = .body) -> Font { .custom("IowanOldStyle-Roman", size: size, relativeTo: style) }
        static func bold(_ size: CGFloat, _ style: Font.TextStyle = .body) -> Font { .custom("IowanOldStyle-Bold", size: size, relativeTo: style) }
        static func italic(_ size: CGFloat, _ style: Font.TextStyle = .body) -> Font { .custom("IowanOldStyle-Italic", size: size, relativeTo: style) }
    }

    enum Words {
        static let listening = "Listening"
        static let notListening = "Not listening"
        static let startHint = "Open \(LensBrand.name) and start listening."
        static let waiting = "Waiting for speech"
        static let note = "note"
        static let notes = "notes"
        static let paused = "Paused: private screen"
        static let pausedHint = "\(LensBrand.name) reads public media only. Nothing from this screen is kept."
        static let openMedia = "Open any video or news app."
        static let footnoteThis = "Footnote this"
        static let reading = "Reading..."
        static let noNote = "No note."
        static let notSure = "Not sure."
        static let noNoteWhy = "No known technique just now."
        static let nothingHeard = "Nothing heard just now."
        static let startBroadcast = "Start the broadcast in \(LensBrand.name)."
        static let startBroadcastHint = "Tap to open \(LensBrand.name). iOS asks once per broadcast."
        static let unsure = "Not sure about this line."
        static let endOfPage = "A footnote is not a verdict."
        /// A fact-check: what sources say about a claim. Never "true" or "false", never a verdict colour.
        static let checking = "Checking..."
        static let checked = "Checked"
        // A check in words about the sources, never "true", "false" or "fake". Stance "differ" means the sources
        // online contradict the claim.
        static let contradicted = "Contradicted"
        static let sourcesContradict = "Sources contradict this"
        static let sourcesAgree = "Sources agree"
        static let sourcesSupport = "Sources support this"
        static let sourcesMixed = "Sources mixed"
        static let sourcesAreMixed = "Sources are mixed"
        static let noSources = "No sources"
        static let noSourcesFound = "No sources found"
        static let justChecked = "Just checked"
        static let factCheck = "Fact check"
        static let checkingClaim = "Checking a claim..."
        static let lockListening = "Checks the facts in what you watch. A claim that sources contradict shows here."
        static let laterNote = "A later note"
        static let thisVideo = "This video"
        static let sources = "Sources"
    }

    static func notes(_ n: Int) -> String { "\(n) \(n == 1 ? Words.note : Words.notes)" }

    /// "2 claims checked, 1 contradicted", "1 claim checked" or "No claim checked".
    static func claims(_ n: Int, contradicted: Int) -> String {
        guard n > 0 else { return "No claim checked" }
        let checked = "\(n) \(n == 1 ? "claim" : "claims") checked"
        return contradicted > 0 ? "\(checked), \(contradicted) contradicted" : checked
    }

    /// "Earlier video: @name, TikTok" or "Earlier video: “They call it a peace...”".
    static func earlierVideo(_ origin: String?) -> String {
        guard let origin, !origin.isEmpty else { return "Earlier video" }
        return "Earlier video: \(origin)"
    }

    /// The app name for a section head, from the server's `app` id ("tiktok", "youtube", "news").
    static func appName(_ id: String?) -> String? {
        guard let id, !id.isEmpty else { return nil }
        switch id.lowercased() {
        case "tiktok": return "TikTok"
        case "youtube", "youtube_shorts", "shorts": return "YouTube"
        case "instagram", "reels": return "Instagram"
        case "x", "twitter": return "X"
        case "facebook": return "Facebook"
        case "safari", "browser", "web": return "Web"
        case "news", "article": return "News"
        case "other", "unknown", "private", "home": return nil
        default: return id.prefix(1).uppercased() + id.dropFirst()
        }
    }

    /// The ten UNLP techniques with the brand's words: the fallback when a result has no label or reason.
    /// `short` fits the compact Island, `why` is "Label. Reason." for the Island and the Lock Screen, `explain` is for
    /// the note detail.
    struct Technique {
        let id: String
        let label: String
        let short: String
        let why: String
        let explain: String
    }

    static let techniques: [Technique] = [
        Technique(id: "loaded_language", label: "Loaded language", short: "Loaded", why: "Strong words, no source.",
                  explain: "Strong, emotional words carry the sentence, and it names no source."),
        Technique(id: "glittering_generalities", label: "Glittering generalities", short: "Big words", why: "Big words, no content.",
                  explain: "Grand, positive words that sound good but say nothing you can check."),
        Technique(id: "euphoria", label: "Euphoria", short: "Euphoria", why: "Good mood, no reason.",
                  explain: "It builds a happy, proud mood that has no reason in the facts."),
        Technique(id: "appeal_to_fear", label: "Appeal to fear", short: "Fear", why: "Fear before thought.",
                  explain: "It names a danger to you and gives no reason to expect it."),
        Technique(id: "fud", label: "Doubt", short: "Doubt", why: "Doubt, no evidence.",
                  explain: "It casts doubt on a person or a source and shows no evidence for the doubt."),
        Technique(id: "bandwagon", label: "Bandwagon", short: "Bandwagon", why: "Says everyone agrees.",
                  explain: "It says that everyone already agrees, so you should join."),
        Technique(id: "cliche", label: "Cliche", short: "Cliche", why: "A phrase that ends thought.",
                  explain: "A stock phrase that sounds final and stops the question."),
        Technique(id: "whataboutism", label: "Whataboutism", short: "Whatabout", why: "Points at others instead.",
                  explain: "It answers a point by pointing at someone else, not at the point."),
        Technique(id: "cherry_picking", label: "Cherry picking", short: "One side", why: "Shows one side only.",
                  explain: "It picks the facts that fit and leaves out the ones that do not."),
        Technique(id: "straw_man", label: "Straw man", short: "Straw man", why: "Answers a claim nobody made.",
                  explain: "It answers a weaker claim than the one the other side made."),
    ]

    /// Finds a technique by id ("appeal_to_fear", "appeal to fear" and the old "doubt" all match).
    static func technique(_ id: String?) -> Technique? {
        guard let id else { return nil }
        let key = id.lowercased().replacingOccurrences(of: " ", with: "_").replacingOccurrences(of: "-", with: "_")
        return techniques.first { $0.id == key } ?? (key == "doubt" ? techniques.first { $0.id == "fud" } : nil)
    }
}

/// The asterisk: the mark of the product, in Iowan Old Style Bold. It sits high in the font, so it is moved down
/// to the centre of its frame.
struct FootnoteStar: View {
    var color: Color
    var size: CGFloat

    var body: some View {
        Text("*")
            .font(LensBrand.Serif.bold(size * 1.5))
            .foregroundStyle(color)
            .offset(y: size * 0.36)
            .frame(width: size, height: size)
    }
}

/// The short rule above a footnote, as in a printed book.
struct FootnoteRule: View {
    var color: Color
    var width: CGFloat = 40

    var body: some View {
        Rectangle().fill(color).frame(width: width, height: 1)
    }
}

/// Draws the marker stroke behind the lower part of the marked text (about 56 % to 96 % of the line), line by line.
/// Text marked with `FootnoteMarked` gets the stroke; the rest is drawn as usual.
struct FootnoteMarked: TextAttribute {
    /// The newest mark: its stroke draws in once, left to right, with `draw`.
    var fresh = false
}

/// The newest words on the page: they fade in and settle with `settle`.
struct FootnoteFresh: TextAttribute {}

/// A line the detector was not sure about: a dotted underline in soft ink, no marker (brand README, "Not sure").
struct FootnoteUnsure: TextAttribute {}

struct MarkerStrokeRenderer: TextRenderer, Animatable {
    var color: Color = LensBrand.marker
    /// 0 to 1: how far the newest words have faded in and settled.
    var settle: Double = 1
    /// 0 to 1: how much of the newest marker stroke is drawn.
    var draw: Double = 1
    /// How far the newest words rise while they settle (0 with Reduce Motion).
    var rise: CGFloat = 6

    var animatableData: AnimatablePair<Double, Double> {
        get { AnimatablePair(settle, draw) }
        set { settle = newValue.first; draw = newValue.second }
    }

    func draw(layout: Text.Layout, in context: inout GraphicsContext) {
        var fresh: [CGRect] = []
        for line in layout {
            for run in line {
                if run[FootnoteUnsure.self] != nil {
                    let tb = run.typographicBounds
                    let y = tb.rect.minY + tb.ascent + max(2, tb.descent * 0.5)
                    var dots = Path()
                    dots.move(to: CGPoint(x: tb.rect.minX + 1, y: y))
                    dots.addLine(to: CGPoint(x: tb.rect.maxX - 1, y: y))
                    context.stroke(dots, with: .color(LensBrand.softInk),
                                   style: StrokeStyle(lineWidth: max(1.2, tb.ascent * 0.09), lineCap: .round, dash: [0.01, max(3, tb.ascent * 0.28)]))
                }
                guard let mark = run[FootnoteMarked.self] else { continue }
                let r = run.typographicBounds.rect
                let stroke = CGRect(x: r.minX - 1, y: r.minY + r.height * 0.56, width: r.width + 2, height: r.height * 0.40)
                if mark.fresh { fresh.append(stroke) } else { context.fill(Path(stroke), with: .color(color)) }
            }
        }
        // The newest stroke draws in reading order, line after line.
        var left = fresh.reduce(0) { $0 + $1.width } * min(1, max(0, draw))
        for stroke in fresh where left > 0 {
            var part = stroke
            part.size.width = min(stroke.width, left)
            left -= part.width
            context.fill(Path(part), with: .color(color))
        }
        for line in layout {
            for run in line {
                if settle < 1, run[FootnoteFresh.self] != nil {
                    var c = context
                    c.opacity = settle
                    c.translateBy(x: 0, y: (1 - settle) * rise)
                    c.draw(run)
                } else {
                    context.draw(run)
                }
            }
        }
    }
}

extension String {
    /// Nil for an empty string.
    var nilIfEmpty: String? { isEmpty ? nil : self }
}

/// The stance of a check in the brand's words (app and widget; the broadcast extension has no LensBrand).
extension ClaimCheck {
    /// The footnote label: "Sources contradict this", "Sources support this", "Sources are mixed", "No sources found",
    /// or "Checked" without a stance.
    var label: String {
        if status == .nothing { return LensBrand.Words.noSourcesFound }
        switch stance {
        case .differ: return LensBrand.Words.sourcesContradict
        case .support: return LensBrand.Words.sourcesSupport
        case .mixed: return LensBrand.Words.sourcesAreMixed
        case .some(.none): return LensBrand.Words.noSourcesFound
        case nil: return LensBrand.Words.checked
        }
    }

    /// The compact Island: "Contradicted", "Sources agree", "Sources mixed", "No sources" or "Checked".
    var shortLabel: String {
        if status == .nothing { return LensBrand.Words.noSources }
        switch stance {
        case .differ: return LensBrand.Words.contradicted
        case .support: return LensBrand.Words.sourcesAgree
        case .mixed: return LensBrand.Words.sourcesMixed
        case .some(.none): return LensBrand.Words.noSources
        case nil: return LensBrand.Words.checked
        }
    }
}
