import SwiftUI

/// "The page": a session read like a printed page. One section per video: the creator as a small head, the speech
/// as running text in Iowan Old Style, marked sentences with the marker stroke and a number, and the notes of that
/// video under a short rule. A marked sentence or a note opens the note detail.
struct SessionPage: View {
    let session: StoredSession
    /// The newest line, which arrives with a short fade and settle (nil: nothing animates).
    var freshId: Int? = nil
    let onOpen: (PageItem) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 40) {
            ForEach(session.sections) { section in
                VideoSectionView(section: section, session: session, freshId: freshId, onOpen: onOpen)
            }
            if !session.items.isEmpty {
                Text(LensBrand.Words.endOfPage)
                    .font(LensBrand.Serif.italic(15, .footnote))
                    .foregroundStyle(LensBrand.softInk)
                    .padding(.top, -12)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct VideoSectionView: View {
    let section: VideoSection
    let session: StoredSession
    let freshId: Int?
    let onOpen: (PageItem) -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @State private var settle = 1.0
    @State private var draw = 1.0

    /// The footnotes of this video: notes, "not sure" lines and checks that came back, in reading order.
    private var notes: [Footnote] { section.footnotes }

    /// The speech, cut after each sentence whose check is under way, so "Checking a claim..." sits under it.
    private var blocks: [[PageItem]] {
        var out: [[PageItem]] = [[]]
        for item in section.speech {
            out[out.count - 1].append(item)
            if item.checkPending { out.append([]) }
        }
        return out.filter { !$0.isEmpty }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            head
                .padding(.bottom, 12)
            if !section.speech.isEmpty {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                        speech(block)
                            .font(LensBrand.Serif.regular(19, .body))
                            .lineSpacing(8)
                            .tint(LensBrand.ink)
                            .textRenderer(MarkerStrokeRenderer(settle: settle, draw: draw, rise: reduceMotion ? 0 : 6))
                            .fixedSize(horizontal: false, vertical: true)
                        if block.last?.checkPending == true {
                            CheckingLine()
                                .padding(.top, 4)
                                .padding(.bottom, 10)
                        }
                    }
                }
                .environment(\.openURL, OpenURLAction { url in
                    if let id = Int(url.lastPathComponent), let item = section.items.first(where: { $0.id == id }) { onOpen(item) }
                    return .handled
                })
            }
            ForEach(section.screenNotes) { item in
                ScreenQuote(item: item, number: session.number(of: item) ?? 0, fresh: item.id == freshId, settle: settle, draw: draw,
                            rise: reduceMotion ? 0 : 6) { onOpen(item) }
                    .padding(.top, 18)
            }
            if !notes.isEmpty {
                VStack(alignment: .leading, spacing: 2) {
                    FootnoteRule(color: LensBrand.rule, width: 84)
                        .padding(.bottom, 2)
                    ForEach(notes) { note in
                        switch note.kind {
                        case .note:
                            FootnoteRow(item: note.item, number: session.number(of: note.item) ?? 0) { onOpen(note.item) }
                        case .check:
                            CheckRow(item: note.item, number: session.number(of: note.item, .check) ?? 0) { onOpen(note.item) }
                        }
                    }
                }
                .padding(.top, 22)
            }
        }
        .onAppear { playIfFresh() }
        .onChange(of: freshId) { playIfFresh() }
    }

    private var head: some View {
        // At accessibility sizes the handle and the count do not fit on one line: stack them.
        let layout = dynamicTypeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 4))
            : AnyLayout(HStackLayout(alignment: .firstTextBaseline, spacing: 12))
        return layout {
            Text(section.handle ?? "Video")
                .font(.footnote.weight(.semibold))
                .foregroundStyle(LensBrand.ink.opacity(0.72))
                .lineLimit(dynamicTypeSize.isAccessibilitySize ? 3 : 1)
                .truncationMode(.middle)
            if !dynamicTypeSize.isAccessibilitySize { Spacer(minLength: 8) }
            if !section.footnotes.isEmpty {
                Text(LensBrand.notes(section.footnotes.count))
                    .font(.footnote)
                    .foregroundStyle(LensBrand.softInk)
                    .monospacedDigit()
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }

    /// The speech of the video as one Text, so lines wrap like a book.
    private func speech(_ items: [PageItem]) -> Text {
        var out = Text("")
        var previous = ""
        for (i, item) in items.enumerated() {
            let text = item.text.trimmingCharacters(in: .whitespacesAndNewlines)
            let join = i == 0 ? "" : Self.join(after: previous, before: text)
            previous = text
            let fresh = item.id == freshId
            var piece: Text
            // A sentence can have a note and a check that came back later: "1,2".
            let numbers = [session.number(of: item), session.number(of: item, .check)].compactMap { $0 }.map(String.init).joined(separator: ",")
            if item.isMarked, !numbers.isEmpty {
                var (before, sentence, after) = PageText.split(text, around: item.mark)
                // split() trims the sentence, so the space after the text before it is gone: put it back.
                if let last = before.last, !last.isWhitespace { before += " " }
                if let first = after.first, !first.isWhitespace, !first.isPunctuation { after = " " + after }
                var link = AttributedString(sentence)
                link.link = URL(string: "footnote://note/\(item.id)")
                let marked = item.isUnsure
                    ? Text(link).foregroundStyle(LensBrand.ink).customAttribute(FootnoteUnsure())
                    : Text(link).foregroundStyle(LensBrand.ink).customAttribute(FootnoteMarked(fresh: fresh))
                let sup = Text("\u{2009}\(numbers)")
                    .font(.caption2.weight(.semibold))
                    .baselineOffset(8)
                    .foregroundStyle(LensBrand.ink)
                piece = Text("\(Text(join + before).foregroundStyle(LensBrand.softInk))\(marked)\(sup)\(Text(after).foregroundStyle(LensBrand.softInk))")
            } else if item.hasCheck, !numbers.isEmpty {
                // A checked claim with no note: ink and its number, no marker. It opens the check.
                var link = AttributedString(text)
                link.link = URL(string: "footnote://note/\(item.id)")
                let sup = Text("\u{2009}\(numbers)").font(.caption2.weight(.semibold)).baselineOffset(8).foregroundStyle(LensBrand.ink)
                piece = Text("\(Text(join).foregroundStyle(LensBrand.softInk))\(Text(link).foregroundStyle(LensBrand.ink))\(sup)")
            } else {
                piece = Text(join + text).foregroundStyle(LensBrand.softInk)
            }
            if fresh { piece = piece.customAttribute(FootnoteFresh()) }
            out = Text("\(out)\(piece)")
        }
        return out
    }

    /// Chunks are cut at quiet points, not at full stops. When a chunk ends without one and the next starts with a
    /// capital, they are two sentences: close the first, so a mark never starts in the middle of a line of text.
    static func join(after previous: String, before next: String) -> String {
        guard let last = previous.last, let first = next.first else { return " " }
        let closed = ".!?\u{2026}\u{00BB}\"\u{201D}:;".contains(last)
        return !closed && first.isUppercase ? ". " : " "
    }

    private func playIfFresh() {
        guard let freshId, let item = section.items.first(where: { $0.id == freshId }) else { return }
        var t = Transaction()
        t.disablesAnimations = true
        withTransaction(t) {
            settle = 0
            draw = item.isNote ? 0 : 1
        }
        DispatchQueue.main.async {
            withAnimation(.timingCurve(0.23, 1, 0.32, 1, duration: reduceMotion ? 0.2 : 0.4)) { settle = 1 }
            guard item.isNote else { return }
            if reduceMotion {
                withAnimation(.easeOut(duration: 0.2)) { draw = 1 }
            } else {
                withAnimation(.timingCurve(0.77, 0, 0.175, 1, duration: 0.6).delay(0.25)) { draw = 1 }
            }
        }
    }
}

/// A note on text that was on the screen (burned-in text or the caption), shown as a small quote.
private struct ScreenQuote: View {
    let item: PageItem
    let number: Int
    let fresh: Bool
    let settle: Double
    let draw: Double
    let rise: CGFloat
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            VStack(alignment: .leading, spacing: 6) {
                Text("On screen")
                    .font(.footnote)
                    .foregroundStyle(LensBrand.softInk)
                quote
                    .font(LensBrand.Serif.italic(17, .body))
                    .lineSpacing(5)
                    .multilineTextAlignment(.leading)
                    .textRenderer(MarkerStrokeRenderer(settle: fresh ? settle : 1, draw: fresh ? draw : 1, rise: rise))
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.leading, 14)
            .overlay(alignment: .leading) { Rectangle().fill(LensBrand.rule).frame(width: 1) }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("On screen: \(item.text). Note \(number), \(item.isUnsure ? LensBrand.Words.unsure : item.label ?? "").")
        .accessibilityHint("Opens the note")
    }

    private var quote: Text {
        let (_, sentence, _) = PageText.split(PageText.clip(item.text, around: item.mark, limit: 140), around: item.mark)
        let marked = item.isUnsure
            ? Text(sentence).foregroundStyle(LensBrand.ink).customAttribute(FootnoteUnsure())
            : Text(sentence).foregroundStyle(LensBrand.ink).customAttribute(FootnoteMarked(fresh: fresh))
        let sup = Text("\u{2009}\(number)").font(.caption2.weight(.semibold)).baselineOffset(8).foregroundStyle(LensBrand.ink)
        let all = Text("\(marked)\(sup)")
        return fresh ? all.customAttribute(FootnoteFresh()) : all
    }
}

/// "1 Loaded language. Strong words, no source." A whole row is the tap target.
struct FootnoteRow: View {
    let item: PageItem
    let number: Int
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("\(number)")
                    .font(.caption.weight(.semibold))
                    .monospacedDigit()
                    .foregroundStyle(LensBrand.ink)
                    .frame(minWidth: 14, alignment: .leading)
                row
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            .padding(.vertical, 7)
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(PressStyle())
        .accessibilityLabel(item.isUnsure ? "Note \(number). \(LensBrand.Words.unsure)" : "Note \(number). \(item.label ?? ""). \(item.why ?? "")")
        .accessibilityHint("Opens the note")
    }

    /// "Label. Reason." A "not sure" line names no technique.
    private var row: Text {
        if item.isUnsure {
            return Text(LensBrand.Words.unsure).font(LensBrand.Serif.italic(16, .subheadline)).foregroundStyle(LensBrand.softInk)
        }
        return Text("\(Text((item.label ?? "Note") + ".").font(LensBrand.Serif.bold(16, .subheadline)).foregroundStyle(LensBrand.ink))\(Text(item.why.map { " " + $0 } ?? "").font(LensBrand.Serif.regular(16, .subheadline)).foregroundStyle(LensBrand.softInk))")
    }
}

/// "Checking a claim...": quiet, under the sentence, while the server looks for sources.
struct CheckingLine: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var dim = false

    var body: some View {
        Text(LensBrand.Words.checking)
            .font(LensBrand.Serif.italic(15, .footnote))
            .foregroundStyle(LensBrand.softInk)
            .opacity(dim ? 0.6 : 1)
            .onAppear {
                guard !reduceMotion else { return }
                withAnimation(.easeInOut(duration: 1.2).repeatForever(autoreverses: true)) { dim = true }
            }
    }
}

/// "2 Checked. What the sources say." and the sources under it. The row opens the note; a source opens in Safari.
struct CheckRow: View {
    let item: PageItem
    let number: Int
    let onTap: () -> Void

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text("\(number)")
                .font(.caption.weight(.semibold))
                .monospacedDigit()
                .foregroundStyle(LensBrand.ink)
                .frame(minWidth: 14, alignment: .leading)
            VStack(alignment: .leading, spacing: 0) {
                Button(action: onTap) {
                    row
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, minHeight: 30, alignment: .leading)
                        .contentShape(Rectangle())
                }
                .buttonStyle(PressStyle())
                .accessibilityLabel("Note \(number). \(item.check?.label ?? LensBrand.Words.checked). \(item.check?.line ?? "")")
                .accessibilityHint("Opens the note")
                ForEach(item.check?.sources ?? [], id: \.self) { source in
                    SourceLink(source: source, size: 14)
                }
            }
        }
        .padding(.vertical, 7)
    }

    private var row: Text {
        let line = item.check?.line ?? (item.check?.status == .nothing ? CheckText.nothing : "")
        return Text("\(Text((item.check?.label ?? LensBrand.Words.checked) + ".").font(LensBrand.Serif.bold(16, .subheadline)).foregroundStyle(LensBrand.ink))\(Text(line.isEmpty ? "" : " " + line).font(LensBrand.Serif.regular(16, .subheadline)).foregroundStyle(LensBrand.softInk))")
    }
}

enum CheckText {
    /// When the server sends no line for a check that found nothing.
    static let nothing = "No source says anything about this yet."
}

/// One source of a check: "Reuters, 12 Nov 2026" with a small arrow. A tap opens it in Safari.
struct SourceLink: View {
    let source: ClaimCheck.Source
    var size: CGFloat = 14

    var body: some View {
        Button {
            if let url = source.link { UIApplication.shared.open(url) }
        } label: {
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(source.line)
                    .font(.system(size: size))
                    .underline(true, color: LensBrand.rule)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                if source.link != nil {
                    Image(systemName: "arrow.up.right")
                        .font(.system(size: size * 0.7, weight: .semibold))
                }
            }
            .foregroundStyle(LensBrand.softInk)
            .frame(maxWidth: .infinity, minHeight: 32, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(PressStyle())
        .disabled(source.link == nil)
        .accessibilityLabel("Source: \(source.line)")
        .accessibilityHint(source.link == nil ? "" : "Opens in Safari")
    }
}

/// The quiet detail of one note: the technique in plain words, why this sentence, what was on the screen.
struct NoteDetail: View {
    let item: PageItem
    let number: Int?
    var checkNumber: Int? = nil

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                Text([PageItem.techniquesShown ? (item.isMarked ? number : checkNumber).map { "Note \($0)" } : nil, item.handleLine].compactMap { $0 }.joined(separator: ", "))
                    .font(.footnote)
                    .foregroundStyle(LensBrand.softInk)
                    .padding(.bottom, 10)
                Text(item.isUnsure ? "Not sure." : item.isMarked ? (item.label ?? "Note") + "." : item.checkPending ? LensBrand.Words.checking : (item.check?.label ?? LensBrand.Words.checked) + ".")
                    .font(LensBrand.Serif.bold(28, .title))
                    .foregroundStyle(LensBrand.ink)
                    .padding(.bottom, 18)
                sentence
                    .font(LensBrand.Serif.regular(21, .title3))
                    .lineSpacing(6)
                    .textRenderer(MarkerStrokeRenderer())
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.bottom, 28)

                if item.isUnsure {
                    block("Why no note") {
                        Text("\(LensBrand.name) could not tell if this line uses a technique. It names none.")
                    }
                } else if item.isMarked {
                block("Why this sentence") {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(item.explain ?? item.why ?? "")
                        if let mark = item.mark, !mark.isEmpty {
                            Text("The words \u{201C}\(mark)\u{201D} carry it.")
                                .foregroundStyle(LensBrand.softInk)
                        }
                    }
                }
                }
                if let check = item.check {
                    block(PageItem.techniquesShown ? checkNumber.map { "Note \($0), what sources say" } ?? "What sources say" : "What sources report") {
                        VStack(alignment: .leading, spacing: 6) {
                            if check.status == .pending {
                                Text("\(LensBrand.name) is looking for sources. The result comes here and, when sources contradict the claim, in the Dynamic Island.")
                                    .font(LensBrand.Serif.italic(18, .body))
                                    .foregroundStyle(LensBrand.softInk)
                            } else {
                                Text(check.line ?? CheckText.nothing)
                                ForEach(check.sources, id: \.self) { source in
                                    SourceLink(source: source, size: 15)
                                }
                            }
                        }
                    }
                }
                if !PageItem.techniquesShown, item.claim != nil {
                    block("In the video") {
                        Text("\u{201C}\(item.text.trimmingCharacters(in: .whitespacesAndNewlines))\u{201D}")
                            .font(LensBrand.Serif.italic(18, .body))
                    }
                }
                if let screen = onScreen {
                    block("On screen") {
                        Text(screen)
                            .font(LensBrand.Serif.italic(18, .body))
                    }
                }
                FootnoteRule(color: LensBrand.rule)
                    .padding(.top, 6)
                    .padding(.bottom, 12)
                Text(PageItem.techniquesShown ? "This is a note, not a verdict. You decide." : "A footnote is not a verdict. Read the sources.")
                    .font(LensBrand.Serif.italic(16, .footnote))
                    .foregroundStyle(LensBrand.softInk)
            }
            .padding(.horizontal, 24)
            .padding(.top, 28)
            .padding(.bottom, 24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .scrollBounceBehavior(.basedOnSize)
        .background(LensBrand.paper)
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .presentationBackground(LensBrand.paper)
    }

    /// The marked sentence with the same numbers as on the page. A claim with only a check has no marker.
    private var sentence: Text {
        let text = PageItem.techniquesShown ? PageText.split(PageText.clip(item.text, around: item.mark, limit: 220), around: item.mark).1 : item.claimText
        let plain = Text(text).foregroundStyle(LensBrand.ink)
        let contradicted = item.check?.isDone == true && item.check?.stance == .differ
        let marked = !item.isMarked ? (contradicted && !PageItem.techniquesShown ? plain.customAttribute(FootnoteMarked()) : plain)
            : item.isUnsure ? plain.customAttribute(FootnoteUnsure()) : plain.customAttribute(FootnoteMarked())
        let numbers = !PageItem.techniquesShown ? "" : [item.isMarked ? number : nil, item.hasCheck ? checkNumber : nil].compactMap { $0 }.map(String.init).joined(separator: ",")
        guard !numbers.isEmpty else { return marked }
        let sup = Text("\u{2009}\(numbers)").font(.caption.weight(.semibold)).baselineOffset(9).foregroundStyle(LensBrand.ink)
        return Text("\(marked)\(sup)")
    }

    /// Burned-in text and the caption, when the screen had them.
    private var onScreen: String? {
        let parts = [item.fromScreen ? nil : item.onScreen, item.description]
            .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        return parts.isEmpty ? nil : parts.joined(separator: "\n")
    }

    private func block<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.footnote)
                .foregroundStyle(LensBrand.softInk)
            content()
                .font(LensBrand.Serif.regular(18, .body))
                .foregroundStyle(LensBrand.ink)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.bottom, 26)
    }
}

enum PageText {
    /// Splits a chunk into the text before the sentence with the marked words, that sentence, and the rest.
    /// The whole sentence is marked, not single words. Without punctuation the whole chunk is the sentence.
    static func split(_ text: String, around mark: String?) -> (String, String, String) {
        let full = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let mark, !mark.isEmpty, let hit = full.range(of: mark, options: .caseInsensitive) else { return ("", full, "") }
        let enders: Set<Character> = [".", "!", "?", "\u{2026}"]
        var start = full.startIndex
        var i = hit.lowerBound
        while i > full.startIndex {
            let prev = full.index(before: i)
            if enders.contains(full[prev]) { start = i; break }
            i = prev
        }
        var end = full.endIndex
        var j = hit.upperBound
        while j < full.endIndex {
            if enders.contains(full[j]) { end = full.index(after: j); break }
            j = full.index(after: j)
        }
        let before = String(full[full.startIndex..<start])
        let sentence = String(full[start..<end]).trimmingCharacters(in: .whitespaces)
        let after = String(full[end...])
        return (before, sentence, after)
    }

    /// At most `limit` characters around the marked words, cut at spaces, with an ellipsis where it was cut.
    static func clip(_ text: String, around mark: String?, limit: Int) -> String {
        let flat = text.split(whereSeparator: \.isWhitespace).joined(separator: " ")
        guard flat.count > limit else { return flat }
        let chars = Array(flat)
        let at = mark.flatMap { flat.range(of: $0, options: .caseInsensitive) }.map { flat.distance(from: flat.startIndex, to: $0.lowerBound) } ?? 0
        var start = max(0, min(at - limit / 3, chars.count - limit))
        var end = min(chars.count, start + limit)
        while start > 0, chars[start - 1] != " ", start < at { start += 1 }
        while end < chars.count, end > at, chars[end - 1] != " " { end -= 1 }
        return (start > 0 ? "\u{2026}" : "") + String(chars[start..<end]).trimmingCharacters(in: .whitespaces) + (end < chars.count ? "\u{2026}" : "")
    }
}

/// Press feedback for quiet buttons: a slight dim, fast and both ways.
struct PressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .opacity(configuration.isPressed ? 0.55 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

/// The one primary action: ink on paper, full width, at the bottom where a thumb reaches it.
struct PrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .foregroundStyle(LensBrand.paper)
            .multilineTextAlignment(.center)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, minHeight: 54)
            .padding(.horizontal, 16)
            .background(LensBrand.ink, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .animation(.timingCurve(0.23, 1, 0.32, 1, duration: 0.16), value: configuration.isPressed)
    }
}

// MARK: Checked claims

/// The page of a session: the old transcript with technique notes when Debug turns them on, else the checked claims.
struct SessionBody: View {
    let session: StoredSession
    var freshId: Int? = nil
    let onOpen: (PageItem) -> Void

    var body: some View {
        if PageItem.techniquesShown {
            SessionPage(session: session, freshId: freshId, onOpen: onOpen)
        } else {
            ClaimList(session: session, freshId: freshId, onOpen: onOpen)
        }
    }
}

/// The claims of a session, grouped by what sources say: contradicted first, then supported, mixed, nothing found,
/// and the ones still under check. Newest first in each group. A claim opens its note with the sources.
struct ClaimList: View {
    let session: StoredSession
    var freshId: Int? = nil
    let onOpen: (PageItem) -> Void

    enum Group: Int, CaseIterable {
        case contradicted, supported, mixed, nothing, checking

        var title: String {
            switch self {
            case .contradicted: "Sources contradict"
            case .supported: "Sources support"
            case .mixed: "Sources are mixed"
            case .nothing: "No sources found"
            case .checking: "Checking"
            }
        }

        static func of(_ check: ClaimCheck) -> Group {
            if check.status == .pending { return .checking }
            if check.status == .nothing { return .nothing }
            switch check.stance {
            case .differ: return .contradicted
            case .support: return .supported
            case .mixed: return .mixed
            case nil, .some(.none): return .nothing
            }
        }
    }

    private var groups: [(Group, [PageItem])] {
        let claims = session.claims.reversed()
        return Group.allCases.compactMap { group in
            let items = claims.filter { Group.of($0.check!) == group }
            return items.isEmpty ? nil : (group, Array(items))
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 36) {
            ForEach(groups, id: \.0) { group, items in
                VStack(alignment: .leading, spacing: 0) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(group.title)
                            .font(.footnote.weight(.semibold))
                            .foregroundStyle(LensBrand.ink.opacity(0.72))
                        Spacer(minLength: 8)
                        Text("\(items.count)")
                            .font(.footnote)
                            .monospacedDigit()
                            .foregroundStyle(LensBrand.softInk)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityAddTraits(.isHeader)
                    .padding(.bottom, 6)
                    ForEach(items) { item in
                        ClaimRow(item: item, group: group) { onOpen(item) }
                            .transition(.opacity.combined(with: .offset(y: 6)))
                    }
                }
            }
            if !session.claims.isEmpty {
                Text(LensBrand.Words.endOfPage)
                    .font(LensBrand.Serif.italic(15, .footnote))
                    .foregroundStyle(LensBrand.softInk)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .animation(.timingCurve(0.23, 1, 0.32, 1, duration: 0.35), value: session.claims.map { "\($0.id)-\($0.check?.status.rawValue ?? "")" })
    }
}

/// One claim: where it was heard, the claim in the page serif (the marker stroke when sources contradict it), what
/// the sources report under a short rule, and their names. The whole row opens the note.
struct ClaimRow: View {
    let item: PageItem
    let group: ClaimList.Group
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            VStack(alignment: .leading, spacing: 8) {
                if let head = item.handleLine {
                    Text(head)
                        .font(.footnote)
                        .foregroundStyle(LensBrand.softInk)
                        .lineLimit(1)
                }
                claim
                    .font(LensBrand.Serif.regular(19, .body))
                    .lineSpacing(6)
                    .textRenderer(MarkerStrokeRenderer())
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
                if group == .checking {
                    CheckingLine()
                } else if let check = item.check {
                    FootnoteRule(color: LensBrand.rule, width: 40)
                        .padding(.top, 2)
                    Text(check.line ?? CheckText.nothing)
                        .font(LensBrand.Serif.regular(16, .subheadline))
                        .foregroundStyle(LensBrand.softInk)
                        .lineSpacing(3)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                    if !check.sources.isEmpty {
                        Text(check.sources.map(\.title).joined(separator: ", "))
                            .font(.footnote)
                            .foregroundStyle(LensBrand.softInk)
                            .lineLimit(1)
                    }
                }
            }
            .padding(.vertical, 14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(PressStyle())
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens the sources")
    }

    private var claim: Text {
        let text = item.claimText
        return group == .contradicted
            ? Text(text).foregroundStyle(LensBrand.ink).customAttribute(FootnoteMarked())
            : Text(text).foregroundStyle(LensBrand.ink)
    }
}
