import SwiftUI

/// The main screen: the wordmark with the live asterisk, one line of state, the page of this session, and one
/// primary action at the bottom where a thumb reaches it. Debug and past sessions are behind quiet header buttons.
struct ContentView: View {
    @Bindable var model: AppModel
    @State private var path: [Route] = []
    @State private var openNote: OpenNote?
    @State private var showGuide = false
    @State private var nearBottom = true

    enum Route: Hashable {
        case sessions, session(UUID), debug
    }

    var body: some View {
        NavigationStack(path: $path) {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        header
                            .padding(.bottom, 28)
                        StatusBlock(model: model)
                            .padding(.bottom, 40)
                        if let session = model.shown, !session.items.isEmpty {
                            SessionBody(session: session, freshId: model.freshItemId) { item in
                                openNote = OpenNote(item: item, number: session.number(of: item), checkNumber: session.number(of: item, .check))
                            }
                        }
                        Color.clear.frame(height: 1).id("end")
                    }
                    .padding(.horizontal, 24)
                    .padding(.top, 4)
                    .padding(.bottom, 24)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .scrollBounceBehavior(.basedOnSize)
                .onScrollGeometryChange(for: Bool.self) { g in
                    g.contentOffset.y + g.containerSize.height >= g.contentSize.height - 120
                } action: { _, isNear in
                    nearBottom = isNear
                }
                // The newest line arrives at the end of the page: follow it, unless the reader scrolled up.
                .onChange(of: model.freshItemId) {
                    guard model.freshItemId != nil, nearBottom, PageItem.techniquesShown else { return }
                    withAnimation(.timingCurve(0.23, 1, 0.32, 1, duration: 0.35)) { proxy.scrollTo("end", anchor: .bottom) }
                }
            }
            .background(LensBrand.paper.ignoresSafeArea())
            .overlay(alignment: .top) {
                // Paper under the status bar, so the page never runs into the clock and the Island.
                GeometryReader { g in
                    LinearGradient(stops: [.init(color: LensBrand.paper, location: 0),
                                           .init(color: LensBrand.paper, location: 0.7),
                                           .init(color: LensBrand.paper.opacity(0), location: 1)],
                                   startPoint: .top, endPoint: .bottom)
                        .frame(height: g.safeAreaInsets.top + 16)
                        .ignoresSafeArea(edges: .top)
                }
                .allowsHitTesting(false)
            }
            .safeAreaInset(edge: .bottom, spacing: 0) { primary }
            .background(alignment: .bottomTrailing) {
                // The system picker must be in the window, so a button can tap it in code.
                PickerHostView(picker: model.picker.view)
                    .frame(width: 44, height: 44)
                    .opacity(0.02)
                    .allowsHitTesting(false)
            }
            .toolbar(.hidden, for: .navigationBar)
            .navigationDestination(for: Route.self) { route in
                switch route {
                case .sessions: SessionsView(model: model)
                case .session(let id): SessionReader(model: model, id: id)
                case .debug: DebugView(model: model)
                }
            }
        }
        .tint(LensBrand.ink)
        .sheet(item: $openNote) { note in
            NoteDetail(item: note.item, number: note.number, checkNumber: note.checkNumber)
        }
        .sheet(isPresented: $showGuide) {
            StartGuide {
                showGuide = false
                model.startListening()
            } onCancel: {
                showGuide = false
            }
        }
        .fullScreenCover(isPresented: Binding(get: { !model.onboarded }, set: { if !$0 { model.onboarded = true } })) {
            Onboarding {
                model.onboarded = true
                start()
            } onDone: {
                model.onboarded = true
            }
        }
        .sensoryFeedback(trigger: model.current?.footnotes.count ?? 0) { old, new in
            new > old ? .impact(weight: .light) : nil
        }
        .onOpenURL { model.open($0) }
        .onChange(of: model.wantsNoteId) { openWantedNote() }
        .onAppear { openWantedNote() }
        .onChange(of: model.wantsGuide) {
            if model.wantsGuide { model.wantsGuide = false; showGuide = true }
        }
    }

    /// A tap on a check in the Island (footnote://check/<id>): the page, then that note with its sources.
    private func openWantedNote() {
        guard let id = model.wantsNoteId else { return }
        model.wantsNoteId = nil
        // The claim of the check, else the newest claim on the page with a finished check.
        guard let session = model.shown,
              let item = session.items.last(where: { $0.matches(noteId: id) }) ?? session.items.last(where: { $0.check?.isDone == true })
        else { return }
        path = []
        openNote = OpenNote(item: item, number: session.number(of: item), checkNumber: session.number(of: item, .check))
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 0) {
            // The wordmark is a logo: it grows with Dynamic Type only up to a point.
            Wordmark(phase: model.phase)
                .dynamicTypeSize(...DynamicTypeSize.xxxLarge)
            Spacer(minLength: 8)
            Button { path.append(.sessions) } label: {
                Image(systemName: "clock.arrow.circlepath")
                    .font(.system(size: 17, weight: .regular))
                    .frame(width: 44, height: 44)
            }
            .accessibilityLabel("Past sessions")
            Menu {
                Button("How Footnote works") { model.resetOnboarding() }
                Button("Debug") { path.append(.debug) }
            } label: {
                Image(systemName: "ellipsis")
                    .font(.system(size: 17, weight: .regular))
                    .frame(width: 44, height: 44)
            }
            .accessibilityLabel("More")
        }
        .foregroundStyle(LensBrand.ink.opacity(0.72))
        .buttonStyle(PressStyle())
        .padding(.trailing, -10)
    }

    // MARK: The one primary action

    private var primary: some View {
        VStack(spacing: 2) {
            Button(primaryTitle, action: primaryAction)
                .buttonStyle(PrimaryButtonStyle())
            // Stop is secondary: quiet, under the main action, and only while listening.
            if model.listening {
                Button("Stop listening") { model.stopListening() }
                    .font(.subheadline)
                    .foregroundStyle(LensBrand.softInk)
                    .frame(maxWidth: .infinity, minHeight: 44)
                    .buttonStyle(PressStyle())
                    .transition(.opacity)
            }
        }
        .animation(.easeOut(duration: 0.2), value: model.listening)
            .padding(.horizontal, 24)
            .padding(.top, 10)
            .padding(.bottom, 8)
            .background {
                LensBrand.paper
                    .ignoresSafeArea()
                    .overlay(alignment: .top) {
                        // The page fades out under the button instead of ending on a hard edge.
                        LinearGradient(colors: [LensBrand.paper.opacity(0), LensBrand.paper], startPoint: .top, endPoint: .bottom)
                            .frame(height: 24)
                            .offset(y: -24)
                    }
            }
    }

    private var primaryTitle: String {
        switch model.phase {
        case .idle: "Start listening"
        case .waiting: "Open the broadcast sheet"
        case .listening, .paused: model.tiktokOnly ? "Open TikTok" : LensBrand.Words.footnoteThis
        case .offline: "Try again"
        case .ended: "Start again"
        }
    }

    private func primaryAction() {
        switch model.phase {
        case .idle: start()
        case .waiting, .ended: model.showBroadcastPicker()
        case .listening, .paused:
            if model.tiktokOnly { model.openTikTok() } else { Task { await model.footnoteNow() } }
        case .offline: model.retry()
        }
    }

    /// Until a broadcast has run once, a short guide explains the iOS sheet before it appears.
    private func start() {
        if model.broadcastConfirmed { model.startListening() } else { showGuide = true }
    }
}

struct OpenNote: Identifiable {
    let item: PageItem
    let number: Int?
    var checkNumber: Int? = nil
    var id: Int { item.id }
}

/// "Footnote*". The asterisk is the live indicator: it breathes while Footnote listens and stays still otherwise.
private struct Wordmark: View {
    let phase: AppModel.Phase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var breathing: Bool { phase == .listening && !reduceMotion }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 1) {
            Text(LensBrand.name)
                .font(LensBrand.Serif.regular(30, .title))
                .foregroundStyle(LensBrand.ink)
            Text("*")
                .font(LensBrand.Serif.bold(24, .title))
                .baselineOffset(9)
                .foregroundStyle(phase == .listening ? LensBrand.ink : LensBrand.softInk)
                .phaseAnimator([1.0, 0.3]) { star, value in
                    star.opacity(breathing ? value : 1)
                } animation: { _ in
                    .easeInOut(duration: 1.6)
                }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(LensBrand.name), \(phase == .listening ? "listening" : phase == .paused ? "paused" : phase == .idle ? "not listening" : "waiting")")
        .accessibilityAddTraits(.isHeader)
    }
}

/// The state in one serif line and one quiet line, with Stop (or Clear for the demo) on the right.
private struct StatusBlock: View {
    @Bindable var model: AppModel

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            VStack(alignment: .leading, spacing: 8) {
                Text(copy.title)
                    .font(LensBrand.Serif.regular(26, .title2))
                    .foregroundStyle(LensBrand.ink)
                    .contentTransition(.opacity)
                Text(copy.detail)
                    .font(.subheadline)
                    .foregroundStyle(LensBrand.softInk)
                    .lineSpacing(2)
                    .contentTransition(.opacity)
            }
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityElement(children: .combine)
            Spacer(minLength: 0)
            if model.demo != nil {
                quiet("Clear") { model.clearDemo() }
            }
        }
        .animation(.easeOut(duration: 0.2), value: copy.title)
    }

    private func quiet(_ title: String, action: @escaping () -> Void) -> some View {
        Button(title, action: action)
            .font(.subheadline)
            .foregroundStyle(LensBrand.softInk)
            .frame(minWidth: 44, minHeight: 44)
            .contentShape(Rectangle())
            .buttonStyle(PressStyle())
    }

    private var copy: (title: String, detail: String) {
        let what = "\(LensBrand.name) checks the facts in what you watch. When sources contradict a claim, it tells you later."
        if let demo = model.demo {
            return ("Demo session.", "\(demo.claimsLine). Not real data.")
        }
        let session = model.current
        let claims = session?.claims.count ?? 0
        let heard = !(session?.items.isEmpty ?? true)
        switch model.phase {
        case .idle:
            if heard, let session, claims > 0 {
                return ("Not listening.", "Last session: \(session.claimsLine.prefix(1).lowercased() + session.claimsLine.dropFirst()).")
            }
            return ("Not listening.", what)
        case .waiting:
            return ("Waiting for the broadcast.", "In the iOS sheet, choose \(LensBrand.name) and tap Start Broadcast.")
        case .listening:
            let open = model.tiktokOnly ? "Open TikTok." : LensBrand.Words.openMedia
            let press = model.tiktokOnly ? "" : " Press the Action button to see a check now."
            if claims > 0, let session { return ("Listening.", session.claimsLine + "." + press) }
            return ("Listening. " + open, (heard ? "No claim checked yet." : "Claims you hear are checked against sources online.") + press)
        case .paused:
            if model.tiktokOnly {
                return ("Paused: not TikTok.", "\(LensBrand.name) is set to read TikTok only. Nothing from other apps is kept.")
            }
            return ("Paused: private screen.", "Messages, mail, banking, passwords, settings and the home screen are private. Nothing from them is kept.")
        case .offline:
            return ("No connection.", "\(LensBrand.name) cannot reach its server. Checks come back when it does.")
        case .ended:
            return ("The broadcast ended.", heard ? "Start it again to keep listening. The checks so far stay below." : "Start it again to keep listening.")
        }
    }
}

// MARK: The start guide

/// Shown once before the iOS broadcast sheet, so the sheet is not a surprise.
struct StartGuide: View {
    let onContinue: () -> Void
    let onCancel: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    Text("iOS asks next.")
                        .font(LensBrand.Serif.regular(28, .title))
                        .foregroundStyle(LensBrand.ink)
                        .padding(.bottom, 12)
                    Text("A system sheet opens. Choose \(LensBrand.name), then tap Start Broadcast. A red pill shows in the corner while \(LensBrand.name) listens.")
                        .font(LensBrand.Serif.regular(18, .body))
                        .foregroundStyle(LensBrand.ink)
                        .lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.bottom, 24)
                    SheetSketch()
                }
                .padding(.horizontal, 24)
                .padding(.top, 32)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .scrollBounceBehavior(.basedOnSize)
            VStack(spacing: 2) {
                Button("Continue", action: onContinue)
                    .buttonStyle(PrimaryButtonStyle())
                Button("Not now", action: onCancel)
                    .font(.subheadline)
                    .foregroundStyle(LensBrand.softInk)
                    .frame(maxWidth: .infinity, minHeight: 44)
                    .buttonStyle(PressStyle())
            }
            .padding(.horizontal, 24)
            .padding(.bottom, 8)
        }
        .background(LensBrand.paper)
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .presentationBackground(LensBrand.paper)
    }
}

/// A small drawing of the iOS sheet: the app row with its check, and the Start Broadcast button.
private struct SheetSketch: View {
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(LensBrand.name).font(.body)
                Spacer()
                Image(systemName: "checkmark").font(.body.weight(.semibold))
            }
            .padding(.horizontal, 16)
            .frame(minHeight: 48)
            Rectangle().fill(LensBrand.ink.opacity(0.12)).frame(height: 1)
            Text("Start Broadcast")
                .font(.body.weight(.semibold))
                .frame(maxWidth: .infinity, minHeight: 48)
        }
        .foregroundStyle(LensBrand.ink)
        .background(LensBrand.ink.opacity(0.05), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("The iOS sheet: choose \(LensBrand.name), then Start Broadcast.")
    }
}

// MARK: First run

/// Three quiet steps: what Footnote does, what it sees, how to start. "How Footnote works" opens it again.
struct Onboarding: View {
    let onStart: () -> Void
    let onDone: () -> Void
    @State private var step = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("\(step + 1) of \(Self.steps)")
                    .font(.footnote)
                    .monospacedDigit()
                    .foregroundStyle(LensBrand.softInk)
                Spacer()
                if step < Self.steps - 1 {
                    Button("Skip", action: onDone)
                        .font(.subheadline)
                        .foregroundStyle(LensBrand.softInk)
                        .frame(minWidth: 44, minHeight: 44)
                        .buttonStyle(PressStyle())
                }
            }
            .frame(minHeight: 44)
            .padding(.horizontal, 24)

            ScrollView {
                Group {
                    switch step {
                    case 0: whatItDoes
                    case 1: whatItSees
                    case 2: actionButton
                    default: howToStart
                    }
                }
                .id(step)
                .transition(.opacity.combined(with: .offset(y: reduceMotion ? 0 : 8)))
                .padding(.horizontal, 24)
                .padding(.top, 36)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .scrollBounceBehavior(.basedOnSize)

            VStack(spacing: 2) {
                Button(step < Self.steps - 1 ? "Next" : "Start listening") {
                    if step < Self.steps - 1 {
                        withAnimation(.timingCurve(0.23, 1, 0.32, 1, duration: 0.3)) { step += 1 }
                    } else {
                        onStart()
                    }
                }
                .buttonStyle(PrimaryButtonStyle())
                Button(step < Self.steps - 1 ? " " : "Later", action: onDone)
                    .font(.subheadline)
                    .foregroundStyle(LensBrand.softInk)
                    .frame(maxWidth: .infinity, minHeight: 44)
                    .buttonStyle(PressStyle())
                    .opacity(step < Self.steps - 1 ? 0 : 1)
                    .disabled(step < Self.steps - 1)
                    .accessibilityHidden(step < Self.steps - 1)
            }
            .padding(.horizontal, 24)
            .padding(.bottom, 8)
        }
        .background(LensBrand.paper.ignoresSafeArea())
        .tint(LensBrand.ink)
    }

    static let steps = 4

    private func title(_ text: String) -> some View {
        Text(text)
            .font(LensBrand.Serif.regular(32, .largeTitle))
            .foregroundStyle(LensBrand.ink)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.bottom, 14)
            .accessibilityAddTraits(.isHeader)
    }

    private func body(_ text: String) -> some View {
        Text(text)
            .font(LensBrand.Serif.regular(18, .body))
            .foregroundStyle(LensBrand.ink)
            .lineSpacing(4)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var whatItDoes: some View {
        VStack(alignment: .leading, spacing: 0) {
            title("Footnotes for what you watch.")
            body("\(LensBrand.name) checks facts in the videos you watch, in any app. When sources online contradict a claim, it gets a footnote with the sources.")
                .padding(.bottom, 36)
            ClaimList(session: Self.example) { _ in }
                .allowsHitTesting(false)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Example: \u{201C}\(DemoSession.claim)\u{201D} Note 1: \(DemoSession.check.label). \(DemoSession.check.line ?? "")")
        }
    }

    private var whatItSees: some View {
        VStack(alignment: .leading, spacing: 0) {
            title("It reads public media only.")
            VStack(alignment: .leading, spacing: 18) {
                point("ear", "While the broadcast runs, \(LensBrand.name) hears the sound of the app in front and sees the screen.")
                point("pause", "On a private screen (messages, mail, banking, passwords, settings, the home screen) it pauses and keeps nothing.")
                point("arrow.up.doc", AppModel.shared.leavesPhone)
            }
        }
    }

    private var actionButton: some View {
        VStack(alignment: .leading, spacing: 0) {
            title("Put \(LensBrand.name) on the Action button.")
            VStack(alignment: .leading, spacing: 18) {
                body("Press it while you watch, and the Dynamic Island shows the check of the last claim at once.")
                numbered(1, "Open Settings, then Action Button.")
                numbered(2, "Swipe to Controls, tap the control under it, and search \u{201C}Footnote this\u{201D}.")
                numbered(3, "Or swipe to Shortcut and choose \u{201C}Footnote this\u{201D}.")
            }
        }
    }

    private var howToStart: some View {
        VStack(alignment: .leading, spacing: 0) {
            title("How to start.")
            VStack(alignment: .leading, spacing: 18) {
                numbered(1, "Tap Start listening.")
                numbered(2, "iOS shows its broadcast sheet. Choose \(LensBrand.name), then tap Start Broadcast.")
                numbered(3, "Open any video or news app. Checks appear on this page, and the Dynamic Island opens when sources contradict a claim.")
            }
        }
    }

    private func point(_ symbol: String, _ text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 14) {
            Image(systemName: symbol)
                .font(.body.weight(.light))
                .foregroundStyle(LensBrand.softInk)
                .frame(width: 22)
                .accessibilityHidden(true)
            body(text)
        }
    }

    private func numbered(_ n: Int, _ text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 14) {
            Text("\(n)")
                .font(.subheadline.weight(.semibold))
                .monospacedDigit()
                .foregroundStyle(LensBrand.softInk)
                .frame(width: 22, alignment: .leading)
            body(text)
        }
        .accessibilityElement(children: .combine)
    }

    /// One video, one checked claim: the real thing at real size.
    static let example: StoredSession = {
        StoredSession(startedAt: .now, items: Array(DemoSession.make().items.prefix(1)), isDemo: true)
    }()
}

// MARK: Past sessions

/// Past sessions by day, newest first, with the number of notes. Swipe to delete.
struct SessionsView: View {
    @Bindable var model: AppModel

    private var days: [(day: Date, sessions: [StoredSession])] {
        let calendar = Calendar.current
        let groups = Dictionary(grouping: model.sessions.filter { !$0.items.isEmpty }) { calendar.startOfDay(for: $0.startedAt) }
        return groups.keys.sorted(by: >).map { ($0, groups[$0]!.sorted { $0.startedAt > $1.startedAt }) }
    }

    var body: some View {
        List {
            if days.isEmpty {
                Text("No sessions yet. A session shows here after \(LensBrand.name) has heard something.")
                    .font(LensBrand.Serif.italic(17, .body))
                    .foregroundStyle(LensBrand.softInk)
                    .listRowBackground(Color.clear)
                    .listRowSeparator(.hidden)
                    .padding(.top, 8)
            }
            ForEach(days, id: \.day) { day in
                Section {
                    ForEach(day.sessions) { session in
                        NavigationLink(value: ContentView.Route.session(session.id)) {
                            SessionRow(session: session)
                        }
                        .listRowBackground(Color.clear)
                        .listRowInsets(EdgeInsets(top: 0, leading: 24, bottom: 0, trailing: 20))
                        .listRowSeparatorTint(LensBrand.ink.opacity(0.12))
                    }
                    .onDelete { offsets in
                        model.deleteSessions(Set(offsets.map { day.sessions[$0].id }))
                    }
                } header: {
                    Text(Self.dayTitle(day.day))
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(LensBrand.softInk)
                        .textCase(nil)
                        .padding(.leading, 8)
                }
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .background(LensBrand.paper.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Text("Sessions")
                    .font(LensBrand.Serif.regular(19, .headline))
                    .foregroundStyle(LensBrand.ink)
                    .accessibilityAddTraits(.isHeader)
            }
        }
        .toolbarBackground(LensBrand.paper, for: .navigationBar)
    }

    static func dayTitle(_ day: Date) -> String {
        let calendar = Calendar.current
        if calendar.isDateInToday(day) { return "Today" }
        if calendar.isDateInYesterday(day) { return "Yesterday" }
        return day.formatted(.dateTime.weekday(.wide).day().month(.wide))
    }
}

private struct SessionRow: View {
    let session: StoredSession

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(session.startedAt.formatted(date: .omitted, time: .shortened))
                    .font(LensBrand.Serif.regular(19, .body))
                    .foregroundStyle(LensBrand.ink)
                    .monospacedDigit()
                Text(subtitle)
                    .font(.footnote)
                    .foregroundStyle(LensBrand.softInk)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Spacer(minLength: 8)
            Text(count)
                .font(.subheadline)
                .monospacedDigit()
                .foregroundStyle(session.contradicted == 0 ? LensBrand.softInk : LensBrand.ink)
        }
        .padding(.vertical, 6)
        .accessibilityElement(children: .combine)
    }

    /// The contradicted claims of the session, the fact it is kept for; the old note count with technique notes.
    private var count: String {
        if PageItem.techniquesShown { return LensBrand.notes(session.footnotes.count) }
        return session.contradicted > 0 ? "\(session.contradicted) contradicted" : LensBrand.claims(session.claims.count, contradicted: 0).replacingOccurrences(of: " checked", with: "")
    }

    /// "2 videos, @davejoneswoke and 1 more" in short form.
    private var subtitle: String {
        let handles = session.sections.compactMap(\.handle)
        let videos = session.videoCount == 1 ? "1 video" : "\(session.videoCount) videos"
        guard let first = handles.first else { return videos }
        return handles.count > 1 ? "\(videos), \(first) and \(handles.count - 1) more" : "\(videos), \(first)"
    }
}

/// One past session as a page, read only.
struct SessionReader: View {
    @Bindable var model: AppModel
    let id: UUID
    @State private var openNote: OpenNote?

    var body: some View {
        ScrollView {
            if let session = model.sessions.first(where: { $0.id == id }) {
                VStack(alignment: .leading, spacing: 0) {
                    Text(SessionsView.dayTitle(session.startedAt) + ", " + session.startedAt.formatted(date: .omitted, time: .shortened))
                        .font(LensBrand.Serif.regular(26, .title2))
                        .foregroundStyle(LensBrand.ink)
                        .padding(.bottom, 8)
                    Text(PageItem.techniquesShown ? "\(LensBrand.notes(session.footnotes.count)) on \(session.videoCount == 1 ? "1 video" : "\(session.videoCount) videos")." : session.claimsLine + ".")
                        .font(.subheadline)
                        .foregroundStyle(LensBrand.softInk)
                        .padding(.bottom, 40)
                    SessionBody(session: session) { item in
                        openNote = OpenNote(item: item, number: session.number(of: item), checkNumber: session.number(of: item, .check))
                    }
                }
                .padding(.horizontal, 24)
                .padding(.top, 12)
                .padding(.bottom, 32)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .background(LensBrand.paper.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(LensBrand.paper, for: .navigationBar)
        .sheet(item: $openNote) { note in
            NoteDetail(item: note.item, number: note.number, checkNumber: note.checkNumber)
        }
    }
}
