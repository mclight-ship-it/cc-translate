import AppKit

@MainActor
enum StatusItemIcon {
    static func load(bundle: Bundle = .main) -> NSImage? {
        let size = NSSize(width: 18, height: 18)
        let image = NSImage(size: size)
        for (name, pixels) in [("CCTranslateStatusTemplate", 18), ("CCTranslateStatusTemplate-2x", 36)] {
            guard let url = bundle.url(forResource: name, withExtension: "png"),
                  let source = NSImage(contentsOf: url), source.isValid,
                  let representation = source.representations.first as? NSBitmapImageRep,
                  representation.pixelsWide == pixels, representation.pixelsHigh == pixels,
                  representation.hasAlpha else {
                NSLog("CC Translate menu bar template is unavailable; retaining the text menu button.")
                return nil
            }
            representation.size = size
            image.addRepresentation(representation)
        }
        // Only the smile has alpha; AppKit supplies the current menu-bar tint.
        image.isTemplate = true
        image.accessibilityDescription = "CC Translate"
        return image
    }

    static func configure(_ button: NSStatusBarButton, bundle: Bundle = .main) {
        let image = load(bundle: bundle)
        button.image = image
        button.imagePosition = image == nil ? .noImage : .imageOnly
        button.title = image == nil ? "CC" : ""
        button.toolTip = "CC Translate"
        button.setAccessibilityLabel("CC Translate")
    }
}
