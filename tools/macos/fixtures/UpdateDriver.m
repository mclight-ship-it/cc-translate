#import <AppKit/AppKit.h>
#import <Sparkle/Sparkle.h>

@interface FixtureDriver : NSObject <SPUUserDriver>
@property(nonatomic, strong) NSURL *applicationURL;
@property(nonatomic, strong) NSURL *reportURL;
@property(nonatomic, copy) NSString *scenario;
@property(nonatomic, copy) NSString *identifier;
@property(nonatomic, strong) SPUUpdater *updater;
@property(nonatomic, strong) NSRunningApplication *original;
@property(nonatomic, strong) NSMutableArray<NSString *> *events;
@property(nonatomic, strong) NSMutableDictionary *report;
@property(nonatomic, strong) id launchObserver;
@property(nonatomic, copy) NSString *outcome;
@property(nonatomic) BOOL finishing;
@property(nonatomic) BOOL forcedCleanup;
@property(nonatomic) BOOL succeeded;
@end

@implementation FixtureDriver
- (BOOL)writeReport {
    self.report[@"events"] = self.events;
    NSError *error = nil;
    NSData *json = [NSJSONSerialization dataWithJSONObject:self.report
                                                 options:NSJSONWritingPrettyPrinted error:&error];
    BOOL written = json && [json writeToURL:self.reportURL options:NSDataWritingAtomic error:&error];
    if (error) { fprintf(stderr, "%s\n", error.localizedDescription.UTF8String); }
    return written;
}

- (BOOL)record:(NSString *)event {
    [self.events addObject:event];
    if ([self writeReport]) { return YES; }
    self.outcome = @"evidence-write-error";
    [self finish];
    return NO;
}

- (BOOL)ownsApplication:(NSRunningApplication *)application {
    if (application && application == self.original) { return YES; }
    return [application.bundleIdentifier isEqual:self.identifier] &&
        [application.bundleURL.URLByResolvingSymlinksInPath.path isEqual:self.applicationURL.path];
}

- (void)rememberApplication:(NSRunningApplication *)application {
    if (![self ownsApplication:application]) { return; }
    NSMutableArray *pids = self.report[@"owned_pids"];
    NSNumber *pid = @(application.processIdentifier);
    if (![pids containsObject:pid]) { [pids addObject:pid]; }
    if (![self writeReport]) {
        self.outcome = @"evidence-write-error";
        [self finish];
    }
}

- (void)begin {
    NSBundle *host = [NSBundle bundleWithURL:self.applicationURL];
    self.identifier = host.bundleIdentifier;
    if (![self.identifier hasPrefix:@"dev.cc-translate.update-fixture."] ||
        ![host.infoDictionary[@"CFBundleVersion"] isEqual:@"1"]) {
        self.outcome = @"invalid-fixture";
        [self finish];
        return;
    }
    self.report[@"expected_identifier"] = self.identifier;
    self.launchObserver = [[NSWorkspace sharedWorkspace].notificationCenter
        addObserverForName:NSWorkspaceDidLaunchApplicationNotification object:nil
        queue:[NSOperationQueue mainQueue] usingBlock:^(NSNotification *notification) {
            [self rememberApplication:notification.userInfo[NSWorkspaceApplicationKey]];
        }];
    NSWorkspaceOpenConfiguration *configuration = [NSWorkspaceOpenConfiguration configuration];
    configuration.activates = NO;
    configuration.addsToRecentItems = NO;
    configuration.createsNewApplicationInstance = YES;
    self.report[@"launch_requested"] = @YES;
    if (![self writeReport]) {
        self.outcome = @"evidence-write-error";
        [self finish];
        return;
    }
    [[NSWorkspace sharedWorkspace] openApplicationAtURL:self.applicationURL configuration:configuration
                                    completionHandler:^(NSRunningApplication *application, NSError *error) {
        dispatch_async(dispatch_get_main_queue(), ^{
            if (error || !application) {
                self.outcome = @"launch-error";
                self.report[@"launch_failed"] = @YES;
                self.report[@"launch_error"] = error.localizedDescription ?: @"No application";
                [self finish];
                return;
            }
            self.original = application;
            [self rememberApplication:application];
            self.report[@"original_pid"] = @(application.processIdentifier);
            if (![self record:@"launched-original"] || self.finishing) { return; }
            // The host, not this external driver, must be terminated and relaunched.
            self.updater = [[SPUUpdater alloc] initWithHostBundle:host applicationBundle:host
                                                     userDriver:self delegate:nil];
            NSError *startError = nil;
            if (![self.updater startUpdater:&startError]) {
                [self showUpdaterError:startError acknowledgement:^{}];
                return;
            }
            [self.updater checkForUpdates];
        });
    }];
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 90 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{
        if (!self.finishing) {
            self.outcome = @"timeout";
            [self finish];
        }
    });
}

- (NSArray<NSRunningApplication *> *)ownedApplications {
    NSMutableArray *applications = [NSMutableArray array];
    if (!self.original) { return applications; }
    if (!self.original.terminated) { [applications addObject:self.original]; }
    for (NSRunningApplication *application in
         [NSRunningApplication runningApplicationsWithBundleIdentifier:self.identifier]) {
        if (application.processIdentifier != self.original.processIdentifier && [self ownsApplication:application]) {
            [applications addObject:application];
        }
    }
    return applications;
}

- (void)finish {
    if (self.finishing) { return; }
    self.finishing = YES;
    // Allow Launch Services to report Sparkle's new application instance.
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC), dispatch_get_main_queue(), ^{
        [self captureFinalState:0];
    });
}

- (void)captureFinalState:(NSUInteger)attempt {
        NSArray<NSRunningApplication *> *applications = [self ownedApplications];
        BOOL replacementFound = NO;
        for (NSRunningApplication *application in applications) {
            if (application.processIdentifier != self.original.processIdentifier) { replacementFound = YES; }
        }
        if ([self.outcome isEqual:@"installed"] && !replacementFound && attempt < 100) {
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC / 10), dispatch_get_main_queue(), ^{
                [self captureFinalState:attempt + 1];
            });
            return;
        }
        NSDictionary *info = [NSDictionary dictionaryWithContentsOfURL:
            [self.applicationURL URLByAppendingPathComponent:@"Contents/Info.plist"]];
        self.report[@"installed_build"] = info[@"CFBundleVersion"] ?: [NSNull null];
        self.report[@"installed_identifier"] = info[@"CFBundleIdentifier"] ?: [NSNull null];
        self.report[@"original_terminated"] = @(self.original.terminated);
        NSMutableArray *pids = [NSMutableArray array];
        for (NSRunningApplication *application in applications) {
            [self rememberApplication:application];
            [pids addObject:@(application.processIdentifier)];
            [application terminate];
        }
        self.report[@"running_pids_before_cleanup"] = pids;
        [self waitForCleanup:0];
}

- (void)waitForCleanup:(NSUInteger)attempt {
    NSArray<NSRunningApplication *> *applications = [self ownedApplications];
    if (applications.count && attempt == 100) {
        self.forcedCleanup = YES;
        for (NSRunningApplication *application in applications) { [application forceTerminate]; }
    }
    if (applications.count && attempt < 150) {
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC / 10), dispatch_get_main_queue(), ^{
            [self waitForCleanup:attempt + 1];
        });
        return;
    }
    self.report[@"graceful_cleanup"] = @(applications.count == 0 && !self.forcedCleanup);
    self.report[@"cleanup_complete"] = @(applications.count == 0);
    self.report[@"events"] = self.events;
    self.report[@"outcome"] = self.outcome ?: @"unexpected-dismissal";
    self.report[@"scenario"] = self.scenario;
    self.report[@"session_in_progress"] = @(self.updater.sessionInProgress);
    self.succeeded = [self writeReport] && applications.count == 0 && !self.forcedCleanup;
    if (self.launchObserver) {
        [[NSWorkspace sharedWorkspace].notificationCenter removeObserver:self.launchObserver];
    }
    [NSApp stop:nil];
    [NSApp postEvent:[NSEvent otherEventWithType:NSEventTypeApplicationDefined location:NSZeroPoint
                                  modifierFlags:0 timestamp:0 windowNumber:0 context:nil
                                        subtype:0 data1:0 data2:0] atStart:NO];
}

- (void)showUpdatePermissionRequest:(SPUUpdatePermissionRequest *)request
                             reply:(void (^)(SUUpdatePermissionResponse *))reply {
    [self record:@"permission-request"];
    reply([[SUUpdatePermissionResponse alloc] initWithAutomaticUpdateChecks:NO sendSystemProfile:NO]);
}
- (void)showUserInitiatedUpdateCheckWithCancellation:(void (^)(void))cancellation {
    if (![self record:@"checking"]) { cancellation(); }
}
- (void)showUpdateFoundWithAppcastItem:(SUAppcastItem *)item state:(SPUUserUpdateState *)state
                               reply:(void (^)(SPUUserUpdateChoice))reply {
    if (![self record:@"found"]) { reply(SPUUserUpdateChoiceSkip); return; }
    self.report[@"offered_build"] = item.versionString;
    reply(SPUUserUpdateChoiceInstall);
}
- (void)showUpdateReleaseNotesWithDownloadData:(SPUDownloadData *)data {
    [self record:@"release-notes"];
}
- (void)showUpdateReleaseNotesFailedToDownloadWithError:(NSError *)error {
    [self record:@"release-notes-error"];
}
- (void)showUpdateNotFoundWithError:(NSError *)error acknowledgement:(void (^)(void))acknowledgement {
    self.outcome = @"not-found";
    self.report[@"error_code"] = @(error.code);
    acknowledgement();
    [self finish];
}
- (void)showUpdaterError:(NSError *)error acknowledgement:(void (^)(void))acknowledgement {
    [self record:@"error"];
    self.outcome = @"error";
    NSMutableArray *errors = [NSMutableArray array];
    for (NSError *current = error; current; current = current.userInfo[NSUnderlyingErrorKey]) {
        [errors addObject:@{@"domain": current.domain, @"code": @(current.code),
                            @"description": current.localizedDescription}];
    }
    self.report[@"errors"] = errors;
    acknowledgement();
    [self finish];
}
- (void)showDownloadInitiatedWithCancellation:(void (^)(void))cancellation {
    if (![self record:@"download"]) { cancellation(); return; }
    if ([self.scenario isEqual:@"cancel-download"]) {
        self.outcome = @"cancel-download";
        cancellation();
    }
}
- (void)showDownloadDidReceiveExpectedContentLength:(uint64_t)length {
    self.report[@"expected_bytes"] = @(length);
}
- (void)showDownloadDidReceiveDataOfLength:(uint64_t)length {
    self.report[@"downloaded_bytes"] = @([self.report[@"downloaded_bytes"] unsignedLongLongValue] + length);
}
- (void)showDownloadDidStartExtractingUpdate { [self record:@"extracting"]; }
- (void)showExtractionReceivedProgress:(double)progress { }
- (void)showReadyToInstallAndRelaunch:(void (^)(SPUUserUpdateChoice))reply {
    if (![self record:@"ready-to-install"]) { reply(SPUUserUpdateChoiceSkip); return; }
    if ([self.scenario isEqual:@"cancel-install"]) {
        self.outcome = @"cancel-install";
        reply(SPUUserUpdateChoiceSkip);
    } else {
        reply(SPUUserUpdateChoiceInstall);
    }
}
- (void)showInstallingUpdateWithApplicationTerminated:(BOOL)terminated
                        retryTerminatingApplication:(void (^)(void))retry {
    [self record:terminated ? @"installing-terminated" : @"installing-request-quit"];
}
- (void)showUpdateInstalledAndRelaunched:(BOOL)relaunched acknowledgement:(void (^)(void))acknowledgement {
    [self record:@"installed"];
    self.outcome = @"installed";
    self.report[@"relaunched"] = @(relaunched);
    acknowledgement();
    [self finish];
}
- (void)dismissUpdateInstallation {
    [self record:@"dismissed"];
    [self finish];
}
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 4) { return 2; }
        NSApplication *application = [NSApplication sharedApplication];
        [application setActivationPolicy:NSApplicationActivationPolicyAccessory];
        FixtureDriver *driver = [FixtureDriver new];
        driver.applicationURL = [NSURL fileURLWithPath:@(argv[1]) isDirectory:YES].URLByResolvingSymlinksInPath;
        driver.scenario = @(argv[2]);
        driver.reportURL = [NSURL fileURLWithPath:@(argv[3])];
        driver.events = [NSMutableArray array];
        driver.report = [NSMutableDictionary dictionary];
        driver.report[@"owned_pids"] = [NSMutableArray array];
        dispatch_async(dispatch_get_main_queue(), ^{ [driver begin]; });
        [application run];
        return driver.succeeded ? 0 : 1;
    }
}
