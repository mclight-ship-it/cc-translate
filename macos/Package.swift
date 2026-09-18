// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "CCTranslateMac",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "CCTranslateMac", targets: ["CCTranslateMac"]),
        .executable(name: "CCClipboardTestProducer", targets: ["CCClipboardTestProducer"]),
        .library(name: "CCProcessSupport", type: .dynamic, targets: ["CCProcessSupport"]),
        .library(name: "CCTranslateSupport", targets: ["CCTranslateSupport"])
    ],
    targets: [
        .target(name: "CCProcessSupport"),
        .target(name: "CCTranslateSupport", dependencies: ["CCProcessSupport"]),
        .executableTarget(name: "CCTranslateMac", dependencies: ["CCTranslateSupport"]),
        .executableTarget(name: "CCClipboardTestProducer",
                          path: "Tests/CCTranslateSupportTests/Fixtures/ClipboardProducer"),
        .testTarget(name: "CCTranslateSupportTests", dependencies: ["CCTranslateSupport"],
                    exclude: ["Fixtures"]),
        .testTarget(name: "CCTranslateMacTests", dependencies: ["CCTranslateMac", "CCTranslateSupport"])
    ],
    swiftLanguageVersions: [.v5]
)
