//! System notifications: a job finished, an approval requested.
//!
//! On macOS they go through the User Notifications framework (UNUserNotificationCenter). The
//! notification plugin posts through NSUserNotificationCenter, which Apple deprecated in 2018. macOS
//! asks the person once, when Ombro first opens (so they answer while looking at it), and shows the
//! notifications as Ombro's, by the app's own bundle. Elsewhere (Linux) the plugin is used as it is:
//! the desktop's notification server over D-Bus, the same on GNOME, KDE and the rest.

#[cfg(target_os = "macos")]
mod mac {
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::sync::OnceLock;

    use block2::RcBlock;
    use objc2::rc::Retained;
    use objc2::runtime::{Bool, NSObject, NSObjectProtocol, ProtocolObject};
    use objc2::{define_class, msg_send, AllocAnyThread};
    use objc2_foundation::{NSError, NSString};
    use objc2_user_notifications::{
        UNAuthorizationOptions, UNMutableNotificationContent, UNNotification, UNNotificationPresentationOptions,
        UNNotificationRequest, UNUserNotificationCenter, UNUserNotificationCenterDelegate,
    };

    define_class!(
        // Without a delegate macOS drops a notification posted while Ombro is the app in front;
        // this one asks for it to be shown then too.
        #[unsafe(super(NSObject))]
        #[name = "OmbroNotificationDelegate"]
        struct Delegate;

        unsafe impl NSObjectProtocol for Delegate {}

        unsafe impl UNUserNotificationCenterDelegate for Delegate {
            #[unsafe(method(userNotificationCenter:willPresentNotification:withCompletionHandler:))]
            fn will_present(
                &self,
                _center: &UNUserNotificationCenter,
                _notification: &UNNotification,
                handler: &block2::DynBlock<dyn Fn(UNNotificationPresentationOptions)>,
            ) {
                handler.call((UNNotificationPresentationOptions::Banner
                    | UNNotificationPresentationOptions::List
                    | UNNotificationPresentationOptions::Sound,));
            }
        }
    );

    struct Kept(Retained<Delegate>);
    // The delegate holds nothing; the center only reads it, from any thread.
    unsafe impl Send for Kept {}
    unsafe impl Sync for Kept {}

    static DELEGATE: OnceLock<Kept> = OnceLock::new();
    static NEXT: AtomicU64 = AtomicU64::new(1);

    fn center() -> Retained<UNUserNotificationCenter> {
        let center = UNUserNotificationCenter::currentNotificationCenter();
        // The center keeps its delegate weakly: it lives here for as long as the app runs.
        let delegate = DELEGATE.get_or_init(|| {
            let d: Retained<Delegate> = unsafe { msg_send![Delegate::alloc(), init] };
            Kept(d)
        });
        center.setDelegate(Some(ProtocolObject::from_ref(&*delegate.0)));
        center
    }

    fn options() -> UNAuthorizationOptions {
        UNAuthorizationOptions::Alert | UNAuthorizationOptions::Sound
    }

    /// At start: on a new install macOS asks now, while the person is looking at Ombro, rather
    /// than at the end of their first job. After that it remembers the answer and asks nothing.
    pub fn ask() {
        let done = RcBlock::new(|_granted: Bool, _err: *mut NSError| {});
        center().requestAuthorizationWithOptions_completionHandler(options(), &done);
    }

    pub fn post(title: &str, body: &str) {
        let center = center();

        let content = UNMutableNotificationContent::new();
        content.setTitle(&NSString::from_str(title));
        content.setBody(&NSString::from_str(body));
        let id = format!("ombro-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed));
        let request =
            UNNotificationRequest::requestWithIdentifier_content_trigger(&NSString::from_str(&id), &content, None);

        // Asked once: macOS remembers the answer, and calls back at once after that. A refusal is
        // the person's to change in System Settings › Notifications; nothing is posted until then.
        let center_for_post = center.clone();
        // Refused ("not allowed for this application", UNErrorDomain 1) when the switch for Ombro is
        // off in System Settings › Notifications: then nothing is shown, as the person chose.
        let done = RcBlock::new(move |granted: Bool, _err: *mut NSError| {
            if granted.as_bool() {
                center_for_post.addNotificationRequest_withCompletionHandler(&request, None);
            }
        });
        center.requestAuthorizationWithOptions_completionHandler(options(), &done);
    }
}

/// At start, on macOS: ask once for permission to notify (see `mac::ask`). Linux needs no permission.
pub fn ask_at_start() {
    #[cfg(target_os = "macos")]
    mac::ask();
}

/// Show a system notification. On macOS by the User Notifications framework; elsewhere the web view
/// uses the notification plugin and never calls this.
#[tauri::command]
pub fn notify(title: String, body: String) {
    #[cfg(target_os = "macos")]
    mac::post(&title, &body);
    #[cfg(not(target_os = "macos"))]
    let _ = (title, body);
}
