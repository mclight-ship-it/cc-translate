// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "CCTranslateRuntimeHarness",
    platforms: [.macOS(.v14)],
    targets: [
        .target(name: "CCProcessSupport"),
        .target(name: "CCTranslateSupport", dependencies: ["CCProcessSupport"]),
        .target(name: "CCTranslateAppResources"),
        .testTarget(name: "CCTranslateSupportTests", dependencies: ["CCTranslateSupport"]),
        .testTarget(name: "CCTranslateMacTests", dependencies: ["CCTranslateAppResources"],
                    swiftSettings: [.define("CC_TRANSLATE_RESOURCE_HARNESS")])
    ],
    swiftLanguageVersions: [.v5]
)
