#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
};
use std::thread;
use std::time::{Duration, Instant};

use directories::ProjectDirs;
use futures::channel::oneshot;
use iced::widget::{column, container, text};
use iced::{Element, Length, Task, Theme, window};
use iced_wry::{WebViewConfig, WebViewController, webview};

struct App {
    webview: WebViewController,
    window_id: Option<window::Id>,
    backend: Arc<Mutex<Option<Child>>>,
    shutdown: Arc<AtomicBool>,
    status: Option<String>,
    ready: bool,
}

#[derive(Debug, Clone)]
enum Message {
    WindowFound(Option<window::Id>),
    BackendStarted(Result<String, String>),
    WebViewCreated(Result<(), String>),
}

impl App {
    fn new() -> (Self, Task<Message>) {
        let controller = WebViewController::new(
            WebViewConfig::default().html(
                "<!doctype html><html><body style='background:#171717;color:#aaa;font:16px sans-serif'>Starting ClaudeX…</body></html>",
            ),
        );

        (
            Self {
                webview: controller,
                window_id: None,
                backend: Arc::new(Mutex::new(None)),
                shutdown: Arc::new(AtomicBool::new(false)),
                status: None,
                ready: false,
            },
            window::oldest().map(Message::WindowFound),
        )
    }

    fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::WindowFound(Some(id)) => {
                self.window_id = Some(id);
                self.status = Some("Starting the local ClaudeX service…".into());
                start_backend_task(Arc::clone(&self.backend), Arc::clone(&self.shutdown))
            }
            Message::WindowFound(None) => {
                self.status = Some("Could not find the ClaudeX window.".into());
                Task::none()
            }
            Message::BackendStarted(Ok(address)) => {
                let Some(window_id) = self.window_id else {
                    self.status = Some("Could not initialize the ClaudeX window.".into());
                    return Task::none();
                };
                self.webview = WebViewController::new(
                    WebViewConfig::default()
                        .url(address)
                        .devtools(cfg!(debug_assertions)),
                );
                self.status = Some("Opening ClaudeX…".into());
                self.webview.create_task(window_id, Message::WebViewCreated)
            }
            Message::BackendStarted(Err(error)) => {
                self.status = Some(format!("ClaudeX could not start: {error}"));
                Task::none()
            }
            Message::WebViewCreated(Ok(())) => {
                self.webview.take_staged();
                self.ready = true;
                self.status = None;
                Task::none()
            }
            Message::WebViewCreated(Err(error)) => {
                self.status = Some(format!("ClaudeX window failed to open: {error}"));
                Task::none()
            }
        }
    }

    fn view(&self) -> Element<'_, Message> {
        if self.ready {
            column![webview(&self.webview)]
                .width(Length::Fill)
                .height(Length::Fill)
                .into()
        } else {
            container(text(self.status.as_deref().unwrap_or("Starting ClaudeX…")))
                .width(Length::Fill)
                .height(Length::Fill)
                .center(Length::Fill)
                .into()
        }
    }
}

impl Drop for App {
    fn drop(&mut self) {
        self.shutdown.store(true, Ordering::SeqCst);
        if let Ok(mut process) = self.backend.lock()
            && let Some(mut child) = process.take()
        {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn start_backend_task(
    backend: Arc<Mutex<Option<Child>>>,
    shutdown: Arc<AtomicBool>,
) -> Task<Message> {
    let (sender, receiver) = oneshot::channel();
    let spawn_result = thread::Builder::new()
        .name("claudex-backend-startup".into())
        .spawn(move || {
            let result = start_backend_process().and_then(|(mut child, address)| {
                if shutdown.load(Ordering::SeqCst) {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err("Application is closing.".into());
                }
                let mut process = backend
                    .lock()
                    .map_err(|_| "Backend process lock failed.".to_string())?;
                *process = Some(child);
                Ok(address)
            });
            let _ = sender.send(result);
        });

    if let Err(error) = spawn_result {
        return Task::done(Message::BackendStarted(Err(format!(
            "Could not create backend startup thread: {error}"
        ))));
    }

    Task::perform(
        async move {
            receiver
                .await
                .unwrap_or_else(|_| Err("Backend startup was interrupted.".into()))
        },
        Message::BackendStarted,
    )
}

fn project_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .expect("ClaudeX project root is missing")
}

fn app_data_dir() -> Result<PathBuf, String> {
    let dirs = ProjectDirs::from("com", "ClaudeX", "ClaudeX")
        .ok_or("Could not determine the ClaudeX user-data directory.")?;
    let path = dirs.data_dir().to_path_buf();
    std::fs::create_dir_all(&path).map_err(|error| error.to_string())?;
    Ok(path)
}

fn start_backend_process() -> Result<(Child, String), String> {
    let port = portpicker::pick_unused_port().ok_or("Could not reserve a local port.")?;
    #[cfg(debug_assertions)]
    let root = project_root();
    let data_dir = app_data_dir()?.join("data");
    std::fs::create_dir_all(&data_dir).map_err(|error| error.to_string())?;

    #[cfg(debug_assertions)]
    let mut command = {
        #[cfg(windows)]
        let python = root.join(".venv/Scripts/python.exe");
        #[cfg(not(windows))]
        let python = root.join(".venv/bin/python");

        let mut command = if python.is_file() {
            Command::new(python)
        } else if cfg!(windows) {
            Command::new("python")
        } else {
            Command::new("python3")
        };
        command
            .arg(root.join("app.py"))
            .env("CLAUDEX_APP_DIR", &root)
            .env("CLAUDEX_STATIC_DIR", root.join("static"));
        command
    };

    #[cfg(not(debug_assertions))]
    let mut command = {
        let executable = std::env::current_exe()
            .map_err(|error| error.to_string())?
            .parent()
            .ok_or("Could not locate the ClaudeX application directory.")?
            .join("resources/backend")
            .join(if cfg!(windows) {
                "claudex-backend.exe"
            } else {
                "claudex-backend"
            });
        if !executable.is_file() {
            return Err(format!(
                "Packaged Python backend not found: {}",
                executable.display()
            ));
        }
        Command::new(executable)
    };

    #[cfg(debug_assertions)]
    command.current_dir(&root);

    command
        .env("CLAUDEX_PORT", port.to_string())
        .env("CLAUDEX_HOST", "127.0.0.1")
        .env("CLAUDEX_DATA_DIR", &data_dir)
        .env("CLAUDEX_ENV_FILE", app_data_dir()?.join(".env"))
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    let mut child = command
        .spawn()
        .map_err(|error| format!("Could not start the ClaudeX Python backend: {error}"))?;
    let address = format!("127.0.0.1:{port}");
    let deadline = Instant::now() + Duration::from_secs(30);

    while Instant::now() < deadline {
        if std::net::TcpStream::connect(&address).is_ok() {
            return Ok((child, format!("http://{address}")));
        }
        if let Some(status) = child.try_wait().map_err(|error| error.to_string())? {
            return Err(format!(
                "ClaudeX Python backend exited during startup ({status})."
            ));
        }
        thread::sleep(Duration::from_millis(150));
    }

    let _ = child.kill();
    let _ = child.wait();
    Err("ClaudeX backend did not start within 30 seconds.".into())
}

fn main() -> iced::Result {
    iced::application(App::new, App::update, App::view)
        .title("ClaudeX")
        .theme(|_| Theme::Dark)
        .window(iced::window::Settings {
            size: iced::Size::new(1120.0, 780.0),
            min_size: Some(iced::Size::new(720.0, 560.0)),
            ..Default::default()
        })
        .centered()
        .run()
}
