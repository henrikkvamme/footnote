import AVFoundation
import ReplayKit
import SwiftUI

/// What the Island shows now: the compact pill and the expanded view, drawn with the widget's own views.
private struct IslandPreview: View {
    let state: LensState

    var body: some View {
        VStack(spacing: 14) {
            HStack {
                LensCompactLeading(state: state)
                Spacer()
                LensCompactTrailing(state: state)
            }
            .padding(.horizontal, 14)
            .frame(width: 236, height: 37)
            .background(Color.black, in: Capsule())

            LensExpandedBody(state: state)
                .padding(20)
                .background(Color.black, in: RoundedRectangle(cornerRadius: 40, style: .continuous))
            // The Lock Screen, drawn with the widget's own view.
            LensLockScreenView(state: state)
                .background(LensBrand.paper, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
                .accessibilityElement(children: .combine)
                .accessibilityLabel("Lock Screen preview")
        }
        .frame(maxWidth: .infinity)
    }
}

private struct StatusRow: View {
    let on: Bool
    let title: String
    let detail: String

    var body: some View {
        LabeledContent {
            Text(detail).lineLimit(1).minimumScaleFactor(0.8)
        } label: {
            Label(title, systemImage: on ? "circle.fill" : "circle")
        }
    }
}

struct PickerHostView: UIViewRepresentable {
    let picker: RPSystemBroadcastPickerView

    func makeUIView(context: Context) -> UIView {
        let container = UIView()
        picker.frame = CGRect(x: 0, y: 0, width: 44, height: 44)
        container.addSubview(picker)
        return container
    }

    func updateUIView(_ uiView: UIView, context: Context) {}
}

// MARK: Debug

struct DebugView: View {
    @Bindable var model: AppModel
    @State private var log = Log.shared
    @State private var sessionName = UserDefaults.standard.string(forKey: "lensSession") ?? ""

    var body: some View {
        List {
            Section("Status") {
                StatusRow(on: model.activityState == "active", title: "Dynamic Island",
                          detail: model.activityState == "active" ? "On" : (model.activity.enabled ? "Off" : "Live Activities are off in Settings"))
                StatusRow(on: model.broadcastLive, title: "Hearing the other app",
                          detail: model.broadcastLive ? "Broadcast on, level \(String(format: "%.2f", model.broadcast?.rms ?? 0))" : "Broadcast off")
                StatusRow(on: (model.nowPlaying?.reads ?? 0) > 0 && !(model.nowPlaying?.paused ?? false), title: "Reading the screen", detail: screenDetail)
                StatusRow(on: model.serverOK, title: "Server", detail: model.serverOK ? "Connected" : "No answer")
            }

            Section("Island now") {
                IslandPreview(state: model.islandNow)
                    .listRowBackground(Color.clear)
            }

            Section {
                Button(LensBrand.Words.footnoteThis) { Task { await model.footnoteNow() } }
                LabeledContent("Presses", value: "\(model.nowPresses)")
                if !model.lastNow.isEmpty { Text(model.lastNow).font(.caption.monospaced()) }
                Toggle("TikTok only", isOn: $model.tiktokOnly)
            } header: {
                Text("Footnote this and apps")
            } footer: {
                Text("Footnote this asks the server (POST /api/now) for the last seconds. The Action button, the Control and the Island button run the same intent. TikTok only: the old product; other apps pause. Off: any app with public media; private screens pause.")
            }

            Section {
                Button("New take") { Task { await model.newTake() } }
            } header: {
                Text("Take")
            } footer: {
                Text("New take clears the claims and checks of this session and the Island.")
            }

            Section {
                Picker("Speech to text", selection: $model.speechPath) {
                    ForEach(SpeechPath.allCases) { Text($0.title).tag($0) }
                }
                LabeledContent("Extension now", value: model.broadcast?.speechActive ?? "-")
                if !model.speechModels.isEmpty { Text(model.speechModels).font(.caption.monospaced()) }
                Button("Download speech models (en, uk, ru)") { Task { await model.refreshSpeechModels(install: true) } }
                Text(model.leavesPhone).font(.caption)
            } header: {
                Text("Speech")
            } footer: {
                Text("On the phone: the broadcast extension transcribes with SpeechAnalyzer (iOS 26) and sends only text to POST /api/text. English uses SpeechTranscriber, Ukrainian and Russian DictationTranscriber. Auto: sound clips until the server's transcripts show the language of a video, then on the phone. Download the models first.")
            }
            .task { await model.refreshSpeechModels(install: false) }

            Section {
                Picker("Update path", selection: $model.updatePath) {
                    ForEach(UpdatePath.allCases) { Text($0.title).tag($0) }
                }
                LabeledContent("Active now", value: model.pathActive)
                Toggle("Keep the app alive (silent audio)", isOn: $model.keepAlive)
                Picker("Chunk length", selection: $model.chunkSeconds) {
                    ForEach([3.0, 4.0, 5.0, 8.0], id: \.self) { Text("\(Int($0)) s").tag($0) }
                }
                Toggle("Technique notes (old)", isOn: $model.techniqueNotes)
                Toggle("Expand the Island on a new note", isOn: $model.activityAlerts)
                Toggle("Alert sound", isOn: $model.alertSound)
                LabeledContent("Push", value: model.apsEnvironment.map { "\($0), token \(model.pushReady ? "sent" : "not yet")" } ?? "not in this build")
            } header: {
                Text("Live Activity")
            } footer: {
                Text("Footnote checks facts. A claim under check lights the logo, and a claim that sources contradict opens the Island. Footnote this delivers the held check now. Technique notes (old): the technique notes on the page and the Island, as before. Auto: the extension and the app both update. To test one path alone, pick it and start listening again. For the extension alone, also turn off Keep alive.")
            }

            Section {
                Picker("Screen reading", selection: $model.screenPath) {
                    ForEach(ScreenPath.allCases) { Text($0.title).tag($0) }
                }
                Picker("Frames per second", selection: $model.fps) {
                    ForEach([0.5, 1.0, 2.0], id: \.self) { Text($0 == 0.5 ? "0.5" : "\(Int($0))").tag($0) }
                }
                if let beat = model.broadcast {
                    LabeledContent("Extension memory", value: String(format: "%.1f MB, peak %.1f MB", beat.memoryMB ?? 0, beat.memoryPeakMB ?? 0))
                    LabeledContent("Frames sent", value: "\(beat.framesSent ?? 0)")
                }
                if let screen = model.nowPlaying {
                    Text("Now playing: \(screen.app ?? "?") \(screen.handle ?? "") \(screen.description ?? "") \(screen.onScreenText ?? "")")
                        .font(.caption.monospaced())
                }
                Button(model.benchmarkRunning ? "Measuring..." : "Measure screen pipeline (memory, time)") { model.runBenchmark() }
                    .disabled(model.benchmarkRunning)
                ForEach(model.benchmark, id: \.self) { Text($0).font(.caption2.monospaced()) }
            } header: {
                Text("Screen")
            } footer: {
                Text("The broadcast sees the whole screen. On a private screen (or, with TikTok only, in any other app) the server discards the frame, its text and the audio, and the Island shows paused.")
            }

            Section {
                Button("Show demo session") { model.showDemo() }
                Button("Play a note arriving") { model.replayDemoArrival() }
                    .disabled(model.demo == nil)
                Button("Play the demo flow") { model.replayDemoFlow() }
                Button("Press Footnote this") { Task { await model.footnoteNow() } }
                Button("New take") { Task { await model.newTake() } }
                Button("Clear demo") { model.clearDemo() }
                    .disabled(model.demo == nil)
                Button("Show first run again") { model.resetOnboarding() }
            } header: {
                Text("Demo")
            } footer: {
                Text("The demo session is sample data. It is never stored or sent.")
            }

            Section("Live Activity tests") {
                LabeledContent("State", value: model.activityState)
                LabeledContent("Notes / chunks heard", value: "\(model.state.count) / \(model.state.chunks)")
                HStack {
                    Button("Simulate line") { model.simulate(technique: false) }
                    Spacer()
                    Button("Simulate technique") { model.simulate(technique: true) }
                }
                .buttonStyle(.borderless)
            }

            Section("Broadcast") {
                Button("Open the broadcast picker") { model.showBroadcastPicker() }
                if let beat = model.broadcast {
                    Text("Extension: \(beat.state ?? "?"), app audio \(beat.audioApp ?? 0), rms \(String(format: "%.4f", beat.rms ?? 0)), chunks \(beat.chunksSent ?? 0), path \(beat.updatePathActive ?? "-"), visible \(beat.activitiesVisible ?? -1), updates \(beat.activityUpdatesFromExtension ?? 0)")
                        .font(.caption.monospaced())
                } else {
                    Text("No heartbeat from the extension yet.").font(.caption).foregroundStyle(.secondary)
                }
            }

            Section {
                Toggle("PiP caption card", isOn: $model.pipEnabled)
                if model.pipEnabled {
                    CardLayerView(layer: model.pip.displayLayer) { model.pipLayerReady() }
                        .aspectRatio(CardRenderer.size.width / CardRenderer.size.height, contentMode: .fit)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                    Button(model.pipActive ? "Stop PiP card" : "Start PiP card") { model.togglePiP() }
                    LabeledContent("PiP possible", value: model.pipPossible ? "yes" : "no")
                    Picker("Audio session", selection: $model.audioVariant) {
                        ForEach(AudioVariant.allCases) { Text($0.rawValue).tag($0) }
                    }
                    Toggle("Mix off at start, on after PiP starts", isOn: $model.mixAfterPiPStart)
                    Toggle("Deactivate session after PiP starts", isOn: $model.deactivateAfterPiPStart)
                    Toggle("Auto PiP when leaving the app", isOn: $model.autoPiPFromInline)
                }
            } header: {
                Text("Picture in Picture (old test)")
            }

            Section("Log") {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 2) {
                        ForEach(Array(log.lines.reversed().enumerated()), id: \.offset) { _, line in
                            Text(line).font(.caption2.monospaced()).frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }
                .frame(height: 260)
            }

            Section("Build") {
                LabeledContent("Branch", value: model.buildBranch.isEmpty ? "-" : model.buildBranch)
                LabeledContent("Commit", value: model.buildCommit.isEmpty ? "-" : String(model.buildCommit.prefix(12)))
                LabeledContent("Built", value: model.buildTime.isEmpty ? "-" : model.buildTime)
                LabeledContent("Server", value: LensConfig.serverURL.host() ?? "")
                LabeledContent("Session", value: LensConfig.session)
            }

            Section {
                TextField("lens-demo", text: $sessionName)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .onSubmit { UserDefaults.standard.set(sessionName.trimmingCharacters(in: .whitespaces), forKey: "lensSession") }
            } header: {
                Text("Session")
            } footer: {
                Text("The server session the app and the extension use. Empty means lens-demo. It applies at the next launch. tools/replay.py feeds a session without a phone.")
            }
        }
        .navigationTitle("Debug")
    }

    private var screenDetail: String {
        if model.screenPath == .off { return "Off" }
        guard let screen = model.nowPlaying, (screen.reads ?? 0) > 0 else { return model.broadcastLive ? "Waiting for a frame" : "Broadcast off" }
        if screen.paused ?? false { return LensBrand.Words.paused }
        let app = LensBrand.appName(screen.app) ?? screen.app ?? "?"
        if let handle = screen.handle, !handle.isEmpty { return "\(handle), \(app)" }
        return app
    }
}

/// Hosts the AVSampleBufferDisplayLayer. PiP needs the layer in a visible view.
private struct CardLayerView: UIViewRepresentable {
    let layer: AVSampleBufferDisplayLayer
    let onReady: () -> Void

    func makeUIView(context: Context) -> LayerHostView {
        let view = LayerHostView()
        view.hosted = layer
        view.layer.addSublayer(layer)
        DispatchQueue.main.async { onReady() }
        return view
    }

    func updateUIView(_ uiView: LayerHostView, context: Context) {}

    final class LayerHostView: UIView {
        var hosted: CALayer?
        override func layoutSubviews() {
            super.layoutSubviews()
            hosted?.frame = bounds
        }
    }
}
