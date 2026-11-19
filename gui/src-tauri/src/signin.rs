//! Signing in to an online tool (Notion, say) when Ombro runs on a server.
//!
//! The tool's sign-in page sends the browser back to `http://127.0.0.1:<port>/callback`, which
//! is always the computer the browser runs on: this one. So the app listens there, catches the
//! answer whose `state` is the sign-in's (passing over any other), and the web view hands it to the server (POST …/sign-in/answer), which checks it
//! and finishes the sign-in. To you it looks the same as signing in with Ombro on this computer.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use tauri::Manager;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::sync::watch;

/// The answers caught on each port, and the `state` the sign-in is waiting for once known.
#[derive(Default)]
pub struct Catches {
    waiting: Mutex<HashMap<u16, Waiting>>,
}

struct Waiting {
    /// The callbacks' targets (`/callback?code=…&state=…`), oldest first.
    answers: watch::Receiver<Vec<String>>,
    expected: Arc<Mutex<Option<String>>>,
}

/// Answers kept while the sign-in's `state` isn't known yet; older ones go first.
const KEEP: usize = 32;

// The page the browser shows once the answer is caught, written by `python -m ombro.signin_page`
// (the same page Ombro's own listener shows).
const PAGE: &str = include_str!("signin_page.html");

/// The path and query of a request's first line (`GET /callback?code=… HTTP/1.1`).
fn target(head: &str) -> Option<&str> {
    let line = head.lines().next()?;
    let mut parts = line.split_whitespace();
    if parts.next()? != "GET" {
        return None;
    }
    parts.next()
}

fn percent_decode(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'+' => out.push(b' '),
            b'%' if i + 2 < bytes.len() => {
                match std::str::from_utf8(&bytes[i + 1..i + 3]).ok().and_then(|h| u8::from_str_radix(h, 16).ok()) {
                    Some(b) => {
                        out.push(b);
                        i += 2;
                    }
                    None => out.push(b'%'),
                }
            }
            b => out.push(b),
        }
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// The `state` in a callback's query, decoded.
fn state_of(target: &str) -> Option<String> {
    let query = target.split_once('?')?.1;
    query
        .split('&')
        .filter_map(|pair| pair.split_once('='))
        .find(|(k, _)| *k == "state")
        .map(|(_, v)| percent_decode(v))
}

/// Whether a callback answers the sign-in that expects `expected`.
fn answers(target: &str, expected: &str) -> bool {
    state_of(target).as_deref() == Some(expected)
}

/// What the listener does with one request's target.
#[derive(Debug, PartialEq)]
enum Reply {
    /// An answer: kept for the sign-in, and the browser shown the done page.
    Caught,
    /// A callback for some other sign-in (its `state` is wrong): refused, and the listener waits on.
    WrongState,
    NotFound,
}

fn judge(target: Option<&str>, expected: Option<&str>) -> Reply {
    match target {
        // /callback for tool servers; /auth/callback for Sign in with ChatGPT, which asks for it.
        Some(t) if t.starts_with("/callback?") || t.starts_with("/auth/callback?") => match expected {
            Some(e) if !answers(t, e) => Reply::WrongState,
            _ => Reply::Caught,
        },
        _ => Reply::NotFound,
    }
}

/// Answer the browser's requests on `listener` until the answer is taken or `deadline` passes.
async fn listen(
    listener: TcpListener,
    tx: watch::Sender<Vec<String>>,
    expected: Arc<Mutex<Option<String>>>,
    deadline: tokio::time::Instant,
) {
    loop {
        let accepted = tokio::select! {
            a = tokio::time::timeout_at(deadline, listener.accept()) => a,
            // The answer was taken (or the sign-in given up): nobody is waiting any more.
            _ = tx.closed() => break,
        };
        let Ok(Ok((mut stream, _))) = accepted else { break };
        let mut buf = vec![0u8; 16 * 1024];
        let mut got = 0;
        // A browser sends its request at once; a connection that stalls isn't let hold the listener.
        let _ = tokio::time::timeout(Duration::from_secs(5), async {
            while got < buf.len() {
                match stream.read(&mut buf[got..]).await {
                    Ok(0) | Err(_) => break,
                    Ok(n) => {
                        got += n;
                        if buf[..got].windows(4).any(|w| w == b"\r\n\r\n") {
                            break;
                        }
                    }
                }
            }
        })
        .await;
        let head = String::from_utf8_lossy(&buf[..got]).to_string();
        let t = target(&head);
        let want = expected.lock().unwrap().clone();
        match judge(t, want.as_deref()) {
            Reply::Caught => {
                let body = PAGE.as_bytes();
                let reply = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                );
                let _ = stream.write_all(reply.as_bytes()).await;
                let _ = stream.write_all(body).await;
                let caught = t.unwrap_or_default().to_string();
                tx.send_modify(|v| {
                    if v.len() >= KEEP {
                        v.remove(0);
                    }
                    v.push(caught);
                });
            }
            Reply::WrongState => {
                let body = b"This isn't the sign-in Ombro is waiting for.";
                let reply = format!(
                    "HTTP/1.1 400 Bad Request\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                );
                let _ = stream.write_all(reply.as_bytes()).await;
                let _ = stream.write_all(body).await;
            }
            Reply::NotFound => {
                let _ = stream
                    .write_all(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    .await;
            }
        }
    }
}

/// The caught answer for `state`, as the address the browser was sent back to.
fn pick(caught: &[String], state: &str, port: u16) -> Option<String> {
    caught.iter().find(|t| answers(t, state)).map(|t| format!("http://127.0.0.1:{port}{t}"))
}

/// Start listening on a free port for the sign-in's answer. Returns the port. It keeps listening
/// until the answer is taken (sign_in_answer) or ten minutes pass: a callback with the wrong
/// `state`, from another program on this computer say, is refused and doesn't end it (review L3).
#[tauri::command]
pub async fn sign_in_listener(app: tauri::AppHandle) -> Result<u16, String> {
    let listener = TcpListener::bind("127.0.0.1:0").await.map_err(|e| e.to_string())?;
    let port = listener.local_addr().map_err(|e| e.to_string())?.port();
    let (tx, rx) = watch::channel::<Vec<String>>(Vec::new());
    let expected = Arc::new(Mutex::new(None::<String>));
    app.state::<Catches>()
        .waiting
        .lock()
        .unwrap()
        .insert(port, Waiting { answers: rx, expected: expected.clone() });
    // Ten minutes is long enough to sign in; then the listener goes away.
    let deadline = tokio::time::Instant::now() + Duration::from_secs(600);
    tauri::async_runtime::spawn(listen(listener, tx, expected, deadline));
    Ok(port)
}

/// Wait for the answer caught on `port` whose `state` is the sign-in's: the address the browser was
/// sent back to, or None if none came within `seconds`. Answers with another `state` are passed over.
#[tauri::command]
pub async fn sign_in_answer(app: tauri::AppHandle, port: u16, seconds: u64, state: String) -> Result<Option<String>, String> {
    if state.is_empty() {
        return Err("the sign-in has no state to check its answer by".into());
    }
    let found = {
        let waiting = app.state::<Catches>();
        let waiting = waiting.waiting.lock().unwrap();
        waiting.get(&port).map(|w| {
            *w.expected.lock().unwrap() = Some(state.clone());
            w.answers.clone()
        })
    };
    let Some(mut rx) = found else { return Err("no sign-in is being listened for".into()) };
    let answer = match tokio::time::timeout(Duration::from_secs(seconds), rx.wait_for(|v| pick(v, &state, port).is_some())).await {
        Ok(Ok(v)) => pick(&v, &state, port),
        _ => None,
    };
    if answer.is_some() {
        // Dropping the last receiver ends the listener.
        drop(rx);
        app.state::<Catches>().waiting.lock().unwrap().remove(&port);
    }
    Ok(answer)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_the_request_target() {
        assert_eq!(target("GET /callback?code=x&state=y HTTP/1.1\r\nHost: a\r\n\r\n"), Some("/callback?code=x&state=y"));
        assert_eq!(target("POST /callback HTTP/1.1\r\n"), None);
        assert_eq!(target(""), None);
    }

    #[test]
    fn reads_the_state() {
        assert_eq!(state_of("/callback?code=x&state=abc").as_deref(), Some("abc"));
        assert_eq!(state_of("/callback?state=a%2Bb%3D&code=x").as_deref(), Some("a+b="));
        assert_eq!(state_of("/callback?code=x"), None);
        assert_eq!(state_of("/callback"), None);
        assert!(answers("/auth/callback?code=1&state=s1", "s1"));
        assert!(!answers("/callback?code=1&state=s2", "s1"));
        assert!(!answers("/callback?code=1&xstate=s1", "s1"));
    }

    async fn get(port: u16, path: &str) -> String {
        let mut s = tokio::net::TcpStream::connect(("127.0.0.1", port)).await.unwrap();
        s.write_all(format!("GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n").as_bytes()).await.unwrap();
        let mut out = String::new();
        s.read_to_string(&mut out).await.unwrap();
        out
    }

    #[tokio::test]
    async fn keeps_waiting_past_a_wrong_state() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let (tx, mut rx) = watch::channel(Vec::new());
        let expected = Arc::new(Mutex::new(None));
        let deadline = tokio::time::Instant::now() + Duration::from_secs(30);
        let task = tokio::spawn(listen(listener, tx, expected.clone(), deadline));

        // A callback before the state is known is kept, not taken as the answer.
        // A connection that sends nothing doesn't block the others for long.
        let _stalled = tokio::net::TcpStream::connect(("127.0.0.1", port)).await.unwrap();
        assert!(get(port, "/callback?code=early&state=other").await.starts_with("HTTP/1.1 200"));
        *expected.lock().unwrap() = Some("right".into());
        // Another program's guess: refused, and the listener is still there.
        assert!(get(port, "/callback?code=evil&state=wrong").await.starts_with("HTTP/1.1 400"));
        assert!(get(port, "/elsewhere").await.starts_with("HTTP/1.1 404"));
        assert!(get(port, "/callback?code=real&state=right").await.starts_with("HTTP/1.1 200"));

        let v = rx.wait_for(|v| pick(v, "right", port).is_some()).await.unwrap().clone();
        assert_eq!(pick(&v, "right", port).as_deref(), Some(format!("http://127.0.0.1:{port}/callback?code=real&state=right").as_str()));
        assert_eq!(v.len(), 2); // the early one and the real one; never the wrong one
        // Taking the answer (dropping the last receiver) ends the listener.
        drop(rx);
        tokio::time::timeout(Duration::from_secs(5), task).await.unwrap().unwrap();
    }

    #[test]
    fn a_wrong_state_is_refused_once_the_state_is_known() {
        assert_eq!(judge(Some("/callback?code=1&state=bad"), Some("good")), Reply::WrongState);
        assert_eq!(judge(Some("/callback?code=1&state=good"), Some("good")), Reply::Caught);
        // Before the state is known every callback is kept; sign_in_answer picks the right one.
        assert_eq!(judge(Some("/callback?code=1&state=bad"), None), Reply::Caught);
        assert_eq!(judge(Some("/favicon.ico"), Some("good")), Reply::NotFound);
        assert_eq!(judge(None, None), Reply::NotFound);
    }
}
