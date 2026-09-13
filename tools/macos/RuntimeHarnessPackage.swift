// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "CCTranslateRuntimeHarness",
    platforms: [.macOS(.v14)],
    targets: [
        .target(name: "CCProcessSupport"),
        .target(name: "CCTranslateSupport", dependencies: ["CCProcessSupport"]),
        .testTarget(name: "CCTranslateSupportTests", dependencies: ["CCTranslateSupport"])
    ],
    swiftLanguageVersions: [.v5]
)
