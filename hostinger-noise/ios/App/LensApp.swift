import SwiftUI

@main
struct LensApp: App {
    @State private var model = AppModel.shared

    var body: some Scene {
        WindowGroup {
            ContentView(model: model)
                .preferredColorScheme(.light)
        }
    }
}
