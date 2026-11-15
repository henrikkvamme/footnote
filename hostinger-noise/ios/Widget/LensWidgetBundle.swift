import SwiftUI
import WidgetKit

@main
struct LensWidgetBundle: WidgetBundle {
    var body: some Widget {
        LensLiveActivity()
        FootnoteNowControl()
        NewTakeControl()
        ListeningControl()
    }
}
