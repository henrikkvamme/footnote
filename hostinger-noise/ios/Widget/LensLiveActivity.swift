import ActivityKit
import SwiftUI
import WidgetKit

private typealias State = LensActivityAttributes.ContentState

struct LensLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: LensActivityAttributes.self) { context in
            LensLockScreenView(state: context.state)
                .activityBackgroundTint(LensBrand.paper)
                .activitySystemActionForegroundColor(LensBrand.ink)
                .widgetURL(context.state.tapURL)
        } dynamicIsland: { context in
            let state = context.state
            return DynamicIsland {
                DynamicIslandExpandedRegion(.bottom) {
                    // Only the sentence, the rule and the footnote. Keep text off the rounded corners.
                    LensExpandedBody(state: state)
                        .padding(.horizontal, 14)
                        .padding(.top, state.mode == .checked ? 0 : 4)
                        // A check has five lines (claim, note, video and sources): the bottom corners are wide
                        // enough for the short meta row, so it takes less padding there.
                        .padding(.bottom, state.mode == .checked ? 4 : 12)
                }
            } compactLeading: {
                LensCompactLeading(state: state)
                    .padding(.leading, 2)
            } compactTrailing: {
                LensCompactTrailing(state: state)
            } minimal: {
                LensMinimal(state: state)
            }
            .keylineTint(LensBrand.marker)
            .widgetURL(state.tapURL)
            .contentMargins(.horizontal, 10, for: .expanded)
        }
    }
}

#Preview("Lock Screen", as: .content, using: LensActivityAttributes(title: "Footnote")) {
    LensLiveActivity()
} contentStates: {
    State(mode: .listening, line: "So today I want to show you my morning routine", count: 0, chunks: 4, updatedAt: .now)
    State(mode: .found, line: "Only three spots left, act now", count: 2, chunks: 9, flagText: "Only three spots left, act now before it is gone",
          flagMark: "Only", flagLabel: "Scarcity", flagShort: "Scarcity", flagWhy: "Makes it look rare, so it feels more valuable.", updatedAt: .now,
          handle: "shop.deals", videoNotes: 2)
    State(mode: .paused, line: "", count: 2, chunks: 9, updatedAt: .now)
    State(mode: .reading, line: "And I use the word mysterious because things about it just aren't adding up.", count: 0, chunks: 4, updatedAt: .now)
    State(mode: .nothing, line: "", count: 0, chunks: 4, flagText: "So last night around 100 protesters gathered.", flagLabel: "No note.",
          flagWhy: "Nothing in the last seconds uses a known technique.", updatedAt: .now)
    State(mode: .waiting, line: "", count: 0, chunks: 0, updatedAt: .now)
}
