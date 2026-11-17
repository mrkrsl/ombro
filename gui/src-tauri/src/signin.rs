//! Signing in to an online tool (Notion, say) when Ombro runs on a server.
//!
//! The tool's sign-in page sends the browser back to `http://127.0.0.1:<port>/callback`, which
//! is always the computer the browser runs on: this one. So the app listens there, once, catches
//! the answer, and the web view hands it to the server (POST …/sign-in/answer), which checks it
//! and finishes the sign-in. To you it looks the same as signing in with Ombro on this computer.

use std::collections::HashMap;
use std::sync::Mutex;
use std::time::Duration;

use tauri::Manager;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::sync::watch;

#[derive(Default)]
pub struct Catches {
    waiting: Mutex<HashMap<u16, watch::Receiver<Option<String>>>>,
}

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

/// Start listening on a free port for one sign-in answer. Returns the port.
#[tauri::command]
pub async fn sign_in_listener(app: tauri::AppHandle) -> Result<u16, String> {
    let listener = TcpListener::bind("127.0.0.1:0").await.map_err(|e| e.to_string())?;
    let port = listener.local_addr().map_err(|e| e.to_string())?.port();
    let (tx, rx) = watch::channel::<Option<String>>(None);
    app.state::<Catches>().waiting.lock().unwrap().insert(port, rx);
    tauri::async_runtime::spawn(async move {
        // Ten minutes is long enough to sign in; then the listener goes away.
        let deadline = tokio::time::Instant::now() + Duration::from_secs(600);
        loop {
            let accepted = tokio::time::timeout_at(deadline, listener.accept()).await;
            let Ok(Ok((mut stream, _))) = accepted else { break };
            let mut buf = vec![0u8; 16 * 1024];
            let mut got = 0;
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
            let head = String::from_utf8_lossy(&buf[..got]).to_string();
            match target(&head) {
                Some(t) if t.starts_with("/callback?") => {
                    let body = PAGE.as_bytes();
                    let reply = format!(
                        "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                        body.len()
                    );
                    let _ = stream.write_all(reply.as_bytes()).await;
                    let _ = stream.write_all(body).await;
                    let _ = tx.send(Some(format!("http://127.0.0.1:{port}{t}")));
                    break;
                }
                _ => {
                    let _ = stream
                        .write_all(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                        .await;
                }
            }
        }
    });
    Ok(port)
}

/// Wait for the answer caught on `port`: the address the browser was sent back to, or None if
/// none came within `seconds`.
#[tauri::command]
pub async fn sign_in_answer(app: tauri::AppHandle, port: u16, seconds: u64) -> Result<Option<String>, String> {
    let rx = app.state::<Catches>().waiting.lock().unwrap().get(&port).cloned();
    let Some(mut rx) = rx else { return Err("no sign-in is being listened for".into()) };
    let got = tokio::time::timeout(Duration::from_secs(seconds), rx.wait_for(|v| v.is_some())).await;
    let answer = match got {
        Ok(Ok(v)) => v.clone(),
        _ => None,
    };
    if answer.is_some() {
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
}
