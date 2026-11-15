import AVFoundation
import AVKit
import UIKit

/// Owns the AVSampleBufferDisplayLayer and the PiP controller for the caption card.
@MainActor
final class PiPController: NSObject {
    let displayLayer = AVSampleBufferDisplayLayer()
    private var controller: AVPictureInPictureController?
    private var possibleObservation: NSKeyValueObservation?
    private var frameTimer: Timer?
    private var content = CardContent(mode: .waiting, label: "Lens", text: "Waiting for audio...", detail: "")

    private(set) var framesEnqueued = 0
    private(set) var renderFailures = 0
    var onChange: (() -> Void)?
    /// Called when PiP has started; the app uses it for the "change options after start" variants.
    var onDidStart: (() -> Void)?

    var isPossible: Bool { controller?.isPictureInPicturePossible ?? false }
    var isActive: Bool { controller?.isPictureInPictureActive ?? false }
    var lastError: String?

    override init() {
        super.init()
        displayLayer.videoGravity = .resizeAspect
        displayLayer.backgroundColor = UIColor.black.cgColor
    }

    /// Call once the layer is in the view hierarchy.
    func setUp(autoFromInline: Bool) {
        guard AVPictureInPictureController.isPictureInPictureSupported() else {
            log("PiP is not supported on this device")
            return
        }
        enqueue()
        if controller == nil {
            let source = AVPictureInPictureController.ContentSource(sampleBufferDisplayLayer: displayLayer, playbackDelegate: self)
            let pip = AVPictureInPictureController(contentSource: source)
            pip.delegate = self
            pip.requiresLinearPlayback = true
            controller = pip
            possibleObservation = pip.observe(\.isPictureInPicturePossible, options: [.initial, .new]) { [weak self] pip, _ in
                let possible = pip.isPictureInPicturePossible
                Task { @MainActor in
                    log("PiP possible: \(possible)")
                    self?.onChange?()
                }
            }
            log("PiP controller created")
        }
        controller?.canStartPictureInPictureAutomaticallyFromInline = autoFromInline
        startFrameTimer()
    }

    func setAutoFromInline(_ on: Bool) {
        controller?.canStartPictureInPictureAutomaticallyFromInline = on
        log("PiP auto start from inline: \(on)")
    }

    func start() {
        guard let controller else { log("PiP: no controller yet"); return }
        if !controller.isPictureInPicturePossible { log("PiP: not possible right now, trying anyway") }
        controller.startPictureInPicture()
    }

    func stop() {
        controller?.stopPictureInPicture()
    }

    func show(_ new: CardContent) {
        guard new != content else { return }
        content = new
        enqueue()
    }

    private func startFrameTimer() {
        frameTimer?.invalidate()
        // Keep frames coming: PiP shows a stalled state without new frames.
        let timer = Timer(timeInterval: 0.5, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.enqueue() }
        }
        RunLoop.main.add(timer, forMode: .common)
        frameTimer = timer
    }

    private func enqueue() {
        let renderer = displayLayer.sampleBufferRenderer
        if renderer.status == .failed {
            renderFailures += 1
            log("PiP renderer failed: \(renderer.error?.localizedDescription ?? "?"), flushing")
            renderer.flush()
        }
        if renderer.requiresFlushToResumeDecoding { renderer.flush() }
        guard let sample = CardRenderer.sampleBuffer(for: content) else { return }
        renderer.enqueue(sample)
        framesEnqueued += 1
    }
}

extension PiPController: AVPictureInPictureControllerDelegate {
    nonisolated func pictureInPictureControllerWillStartPictureInPicture(_ controller: AVPictureInPictureController) {
        MainActor.assumeIsolated { log("PiP will start") }
    }

    nonisolated func pictureInPictureControllerDidStartPictureInPicture(_ controller: AVPictureInPictureController) {
        MainActor.assumeIsolated {
            log("PiP did start")
            onDidStart?()
            onChange?()
        }
    }

    nonisolated func pictureInPictureController(_ controller: AVPictureInPictureController, failedToStartPictureInPictureWithError error: Error) {
        let text = "\(error.localizedDescription) (\((error as NSError).domain) \((error as NSError).code))"
        MainActor.assumeIsolated {
            lastError = text
            log("PiP failed to start: \(text)")
            onChange?()
        }
    }

    nonisolated func pictureInPictureControllerWillStopPictureInPicture(_ controller: AVPictureInPictureController) {
        MainActor.assumeIsolated { log("PiP will stop") }
    }

    nonisolated func pictureInPictureControllerDidStopPictureInPicture(_ controller: AVPictureInPictureController) {
        MainActor.assumeIsolated {
            log("PiP did stop")
            onChange?()
        }
    }

    nonisolated func pictureInPictureController(
        _ controller: AVPictureInPictureController,
        restoreUserInterfaceForPictureInPictureStopWithCompletionHandler completionHandler: @escaping (Bool) -> Void
    ) {
        completionHandler(true)
    }
}

extension PiPController: AVPictureInPictureSampleBufferPlaybackDelegate {
    nonisolated func pictureInPictureController(_ controller: AVPictureInPictureController, setPlaying playing: Bool) {
        MainActor.assumeIsolated { log("PiP setPlaying \(playing) (ignored, the card is live)") }
    }

    nonisolated func pictureInPictureControllerTimeRangeForPlayback(_ controller: AVPictureInPictureController) -> CMTimeRange {
        // Live content: no scrubber.
        CMTimeRange(start: .negativeInfinity, duration: .positiveInfinity)
    }

    nonisolated func pictureInPictureControllerIsPlaybackPaused(_ controller: AVPictureInPictureController) -> Bool {
        false
    }

    nonisolated func pictureInPictureController(_ controller: AVPictureInPictureController, didTransitionToRenderSize newRenderSize: CMVideoDimensions) {
        MainActor.assumeIsolated { log("PiP render size \(newRenderSize.width)x\(newRenderSize.height)") }
    }

    nonisolated func pictureInPictureController(_ controller: AVPictureInPictureController, skipByInterval skipInterval: CMTime) async {}

    nonisolated func pictureInPictureControllerShouldProhibitBackgroundAudioPlayback(_ controller: AVPictureInPictureController) -> Bool {
        // Do not ask iOS to stop other background audio because of our PiP.
        false
    }
}
