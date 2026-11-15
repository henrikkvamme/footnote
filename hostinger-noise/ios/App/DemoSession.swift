import Foundation

/// A sample session for Debug, screenshots and the demo video, the same flow as the demo on the phone: a claim on a
/// TikTok video by @davejoneswoke that sources contradict, then a later video by @dougwilliams165. It is never stored
/// and never sent.
enum DemoSession {
    /// The claim of the demo, as the first video says it in its first 11 s.
    static let claim = "This is the house of Ukrainian President Zelenskyy."
    /// The claim as the server sends it: one short, complete statement.
    static let shortClaim = "Zelenskyy owns $20 million mansion in Florida"
    static let laterShortClaim = "Zelenska spent $1 million on Cartier jewelry in New York"
    static let claimVideo = 1
    static let laterVideo = 2

    /// The check of the claim when it comes back: contradicted by sources, so the Island shows it.
    static let check = ClaimCheck(
        status: .found,
        line: "The house was still for sale. No county record names Zelenskyy.",
        sources: [.init(title: "Lead Stories", url: "https://leadstories.com/", date: "2023-12"),
                  .init(title: "USA TODAY", url: "https://www.usatoday.com/news/factcheck/"),
                  .init(title: "France 24", url: "https://www.france24.com/en/tag/fact-check/")],
        stance: .differ, notify: true)

    /// The claim of the later video, at 15 s, and its check.
    static let laterClaim = "The wife of Zelensky spends 1 million dollars on Cartier jewelry in New York City."
    static let laterCheck = ClaimCheck(
        status: .found,
        line: "She was in Ottawa that day. The receipt is fake.",
        sources: [.init(title: "Lead Stories", url: "https://leadstories.com/"),
                  .init(title: "DW Fact Check", url: "https://www.dw.com/en/fact-check/t-56584214")],
        stance: .differ, notify: true)

    /// The whole session, with the checks back. `upToClaim` keeps only the lines up to the claim, with its check still
    /// pending: the start of the demo replay.
    static func make(now: Date = .now, upToClaim: Bool = false) -> StoredSession {
        var items: [PageItem] = []
        func line(_ text: String, check: ClaimCheck? = nil, claim: String? = nil) {
            let at = now.addingTimeInterval(Double(items.count - 6) * 5)
            items.append(PageItem(id: items.count + 1, text: text, technique: nil, label: nil, why: nil, explain: nil, mark: nil,
                                  source: "audio", videoId: claimVideo, handle: "davejoneswoke", onScreen: nil, description: nil, at: at,
                                  app: "tiktok", resultId: 100 + items.count, check: check, claim: claim))
        }

        line(claim, check: upToClaim ? ClaimCheck(status: .pending) : check, claim: upToClaim ? nil : shortClaim)
        line("The mansion is worth $20 million.")
        line("Located in Ponte Vedra Beach, Florida.")
        if upToClaim { return StoredSession(startedAt: now.addingTimeInterval(-30), endedAt: nil, items: items, isDemo: true) }

        items += later(now: now, from: items.count, checked: true)
        return StoredSession(startedAt: now.addingTimeInterval(-90), endedAt: nil, items: items, isDemo: true)
    }

    /// The later video, the one in front when the check comes back. In the replay its own claim is not checked yet.
    static func later(now: Date = .now, from count: Int, checked: Bool = false) -> [PageItem] {
        [PageItem(id: count + 1, text: laterClaim, technique: nil, label: nil, why: nil, explain: nil, mark: nil, source: "audio",
                  videoId: laterVideo, handle: "dougwilliams165", onScreen: nil, description: nil, at: now, app: "tiktok",
                  resultId: 100 + count, check: checked ? laterCheck : nil, claim: checked ? laterShortClaim : nil)]
    }
}
