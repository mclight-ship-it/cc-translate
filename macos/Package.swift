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
    dependencies: [
        .package(url: "https://github.com/sparkle-project/Sparkle", exact: "2.10.0")
    ],
    targets: [
        .target(name: "CCProcessSupport"),
        .target(name: "CCTranslateSupport", dependencies: ["CCProcessSupport"]),
        .executableTarget(
            name: "CCTranslateMac",
            dependencies: ["CCTranslateSupport", .product(name: "Sparkle", package: "Sparkle")],
            linkerSettings: [.unsafeFlags(["-Xlinker", "-rpath", "-Xlinker", "@executable_path/../Frameworks"])]
        ),
        .executableTarget(name: "CCClipboardTestProducer",
                          path: "Tests/CCTranslateSupportTests/Fixtures/ClipboardProducer"),
        .testTarget(name: "CCTranslateSupportTests", dependencies: ["CCTranslateSupport"],
                    exclude: ["Fixtures"]),
        .testTarget(name: "CCTranslateMacTests", dependencies: ["CCTranslateMac", "CCTranslateSupport"])
    ],
    swiftLanguageVersions: [.v5]
)
