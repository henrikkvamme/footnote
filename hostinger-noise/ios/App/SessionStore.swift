import Foundation

/// One line of the page: a transcribed sentence or a note on the screen text.
struct PageItem: Codable, Identifiable, Hashable {
    var id: Int
    var text: String
    var technique: String?
    var label: String?
    var why: String?
    var explain: String?
    var mark: String?
    /// "audio" or "screen".
    var source: String
    var videoId: Int?
    var handle: String?
    var onScreen: String?
    var description: String?
    var at: Date
    /// The detector was not sure about this line (the dotted line, no technique name).
    var unsure: Bool? = nil
    /// The app ("tiktok", "youtube", "news") and the author the screen read gave, for the section head.
    var app: String? = nil
    var author: String? = nil
    /// The id of the server result, so a check that comes back later finds its sentence.
    var resultId: Int? = nil
    /// The fact-check of this sentence: pending while the server looks, then a second footnote.
    var check: ClaimCheck? = nil
    /// The claim in this sentence as one short, complete statement, when the server sends it with the check.
    var claim: String? = nil
    /// The id of the server's check item, so a tap on a pushed result (noteId = the check id when the claim has no
    /// sentence) finds this line.
    var checkId: Int? = nil

    /// Whether a tap on footnote://check/<id> means this line: the id of its sentence or of its check.
    func matches(noteId id: Int) -> Bool { id != 0 && (resultId == id || checkId == id) }

    /// What the page and the Island show for a checked sentence: the short claim, else the sentence.
    var claimText: String { (claim ?? text).trimmingCharacters(in: .whitespacesAndNewlines) }

    /// Debug "Technique notes (old)". Off (the product): Footnote checks facts, and the page shows no technique notes.
    static var techniquesShown: Bool { UserDefaults.standard.bool(forKey: "techniqueNotes") }
    var isUnsure: Bool { unsure == true }
    /// A footnote with a technique: counted as a note.
    var isNote: Bool { Self.techniquesShown && technique != nil && !isUnsure }
    /// A line with a footnote: a note or a "not sure" line. Both get a number.
    var isMarked: Bool { Self.techniquesShown && (technique != nil || isUnsure) }
    var fromScreen: Bool { source == "screen" }
    /// A check is under way: the page says "Checking a claim..." under the sentence.
    var checkPending: Bool { check?.status == .pending }
    /// A check came back: it is a footnote of its own.
    var hasCheck: Bool { check?.isDone == true }

    /// The section head: the author (or "@handle"; a display name with spaces stays as it is) and the app,
    /// such as "Sonya Turova, TikTok". Nil when the screen gave neither.
    var handleLine: String? {
        let app = LensBrand.appName(self.app)
        let raw = [author, handle].compactMap { $0?.trimmingCharacters(in: .whitespaces) }.first { !$0.isEmpty }
        guard let raw else { return app }
        let name = raw.hasPrefix("@") || raw.contains(" ") || raw == author ? raw : "@" + raw
        return app.map { "\(name), \($0)" } ?? name
    }
}

/// One video of a session on the page: its creator, its lines and its notes.
struct VideoSection: Identifiable {
    let id: String
    let handle: String?
    let items: [PageItem]
    var speech: [PageItem] { items.filter { !$0.fromScreen } }
    var screenNotes: [PageItem] { items.filter { $0.fromScreen && $0.isMarked } }
    var notes: [PageItem] { items.filter(\.isNote) }
    var marked: [PageItem] { items.filter(\.isMarked) }
    /// The footnotes of this video in reading order: a note, then the check of the same sentence.
    var footnotes: [Footnote] { items.flatMap(Footnote.of) }
}

/// One footnote on the page: the note of a sentence, or the check of a claim that came back later.
struct Footnote: Identifiable {
    enum Kind { case note, check }
    let item: PageItem
    let kind: Kind
    var id: String { "\(item.id)-\(kind)" }

    static func of(_ item: PageItem) -> [Footnote] {
        var out: [Footnote] = []
        if item.isMarked { out.append(Footnote(item: item, kind: .note)) }
        if item.hasCheck { out.append(Footnote(item: item, kind: .check)) }
        return out
    }
}

/// A listening session, kept on the phone.
struct StoredSession: Codable, Identifiable, Hashable {
    var id = UUID()
    var startedAt: Date
    var endedAt: Date?
    var items: [PageItem] = []
    var isDemo = false

    var notes: [PageItem] { items.filter(\.isNote) }
    var marked: [PageItem] { items.filter(\.isMarked) }
    var footnotes: [Footnote] { items.flatMap(Footnote.of) }
    /// The claims of the session: the sentences with a check, done or under way.
    var claims: [PageItem] { items.filter { $0.check != nil } }
    var contradicted: Int { claims.filter { $0.check?.isDone == true && $0.check?.stance == .differ }.count }
    /// "2 claims checked, 1 contradicted" or "No claim checked".
    var claimsLine: String {
        let done = claims.filter { $0.check?.isDone == true }.count
        let pending = claims.count - done
        if done == 0, pending > 0 { return pending == 1 ? "Checking a claim" : "Checking \(pending) claims" }
        return LensBrand.claims(done, contradicted: contradicted)
    }

    /// Consecutive items of one video form a section. Lines without a video id join the section before them.
    var sections: [VideoSection] {
        var out: [VideoSection] = []
        var current: [PageItem] = []
        var currentVideo: Int?
        func flush() {
            guard !current.isEmpty else { return }
            let handle = current.compactMap(\.handleLine).first
            out.append(VideoSection(id: "\(out.count)-\(currentVideo ?? -1)", handle: handle, items: current))
            current = []
        }
        for item in items {
            if let video = item.videoId, let open = currentVideo, video != open { flush() }
            if let video = item.videoId { currentVideo = video }
            current.append(item)
        }
        flush()
        return out
    }

    var videoCount: Int { sections.count }

    /// Which video a line is from, for a later note: the section head, else the first words of the video.
    func origin(of item: PageItem) -> String? {
        guard let section = sections.first(where: { $0.items.contains { $0.id == item.id } }) else { return item.handleLine }
        if let head = section.handle { return head }
        guard let first = section.speech.first?.text.split(separator: " ").prefix(5).joined(separator: " "), !first.isEmpty else { return nil }
        return "\u{201C}\(first)...\u{201D}"
    }

    /// The global number of a footnote on the page (1, 2, 3 in reading order). A check counts as a footnote.
    func number(of item: PageItem, _ kind: Footnote.Kind = .note) -> Int? {
        footnotes.firstIndex(where: { $0.item.id == item.id && $0.kind == kind }).map { $0 + 1 }
    }

    /// Puts a check on its sentence: by the result id, else by the words of the claim. Returns the item when the
    /// check changed something.
    mutating func apply(_ c: CheckResult) -> PageItem? {
        guard let index = index(of: c) else { return nil }
        let short = c.claim?.trimmingCharacters(in: .whitespacesAndNewlines)
        let claim = short.flatMap { $0.isEmpty || Self.normalized($0) == Self.normalized(items[index].text) ? nil : $0 }
        let newClaim = claim != nil && items[index].claim != claim
        // A late "pending" never replaces a result.
        let newCheck = items[index].check != c.check && !(c.check.status == .pending && items[index].check?.isDone == true)
        let newCheckId = c.id != nil && items[index].checkId != c.id
        guard newCheck || newClaim || newCheckId else { return nil }
        if newCheck { items[index].check = c.check }
        if newCheckId { items[index].checkId = c.id }
        if let claim { items[index].claim = claim }
        return items[index]
    }

    /// The line a check belongs to: by the result id, by the check id, else by the words of the claim.
    func index(of c: CheckResult) -> Int? {
        c.of.flatMap { id in items.lastIndex(where: { $0.resultId == id }) }
            ?? c.id.flatMap { id in items.lastIndex(where: { $0.checkId == id }) }
            ?? c.claim.map(Self.normalized).flatMap { claim in
                claim.count < 8 ? nil : items.lastIndex(where: {
                    let text = Self.normalized($0.text)
                    return text.count >= 8 && (text.contains(claim) || claim.contains(text))
                })
            }
    }

    /// Adds a server result unless it repeats a line already on the page. Returns true when something was added.
    mutating func add(_ r: TranscriptResult, at date: Date = .now) -> Bool {
        let text = r.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard (r.speech || r.technique != nil || r.unsure == true), !text.isEmpty, text != "[no speech]" else { return false }
        let key = Self.normalized(text)
        guard key.count > 1 else { return false }
        for old in items.suffix(24) {
            let oldKey = Self.normalized(old.text)
            if oldKey == key { return false }
            // A chunk that only repeats part of the line before it (or the other way round) adds nothing.
            if key.count >= 24, oldKey.contains(key) { return false }
        }
        // Label and reasons come from the result; the shared table fills what a result leaves out.
        let known = LensBrand.technique(r.technique)
        let item = PageItem(id: (items.map(\.id).max() ?? 0) + 1, text: text, technique: r.technique, label: r.label ?? known?.label,
                            why: r.why ?? known?.why, explain: r.explain ?? known?.explain, mark: r.mark,
                            source: r.source ?? "audio", videoId: r.videoId, handle: r.handle, onScreen: r.onScreen, description: r.description,
                            at: date, unsure: r.unsure, app: r.app, author: r.author, resultId: r.id, check: r.check)
        items.append(item)
        return true
    }

    /// A check of a claim the page does not have yet ("Footnote this" on words still being transcribed): the claim
    /// becomes a line of its own, with the check. Returns the new item.
    mutating func addClaim(_ c: CheckResult, at date: Date = .now) -> PageItem? {
        guard let claim = c.claim?.trimmingCharacters(in: .whitespacesAndNewlines), claim.count > 1 else { return nil }
        if let id = c.of, items.contains(where: { $0.resultId == id }) { return nil }
        let item = PageItem(id: (items.map(\.id).max() ?? 0) + 1, text: claim, source: "audio", videoId: c.videoId ?? items.last?.videoId,
                            handle: c.handle ?? items.last?.handle, at: date, app: items.last?.app, resultId: c.of ?? c.id, check: c.check,
                            checkId: c.id)
        items.append(item)
        return item
    }

    static func normalized(_ text: String) -> String {
        let folded = text.lowercased().unicodeScalars.map { CharacterSet.alphanumerics.contains($0) ? Character($0) : " " }
        return String(folded).split(separator: " ").joined(separator: " ")
    }
}

/// Keeps the sessions in one JSON file in Application Support, written after each change. An install or a crash
/// kills the app at any time, so nothing waits for a clean exit.
@MainActor
final class SessionStore {
    private(set) var sessions: [StoredSession] = []
    private let url: URL
    private let limit = 60

    init() {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        url = dir.appendingPathComponent("sessions.json")
        if let data = try? Data(contentsOf: url) {
            let decoder = JSONDecoder()
            decoder.dateDecodingStrategy = .iso8601
            sessions = (try? decoder.decode([StoredSession].self, from: data)) ?? []
        }
    }

    func upsert(_ session: StoredSession) {
        guard !session.isDemo else { return }
        if let i = sessions.firstIndex(where: { $0.id == session.id }) {
            sessions[i] = session
        } else {
            sessions.insert(session, at: 0)
        }
        // A session with nothing heard is not worth a row.
        sessions = Array(sessions.filter { !$0.items.isEmpty || $0.id == session.id }.prefix(limit))
        save()
    }

    func delete(_ ids: Set<UUID>) {
        sessions.removeAll { ids.contains($0.id) }
        save()
    }

    private func save() {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        guard let data = try? encoder.encode(sessions.filter { !$0.items.isEmpty }) else { return }
        try? data.write(to: url, options: .atomic)
    }
}
