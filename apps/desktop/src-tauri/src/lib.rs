use std::{
    net::TcpStream,
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};

use tauri::{AppHandle, Manager, State};

struct BackendProcess {
    child: Mutex<Option<Child>>,
    port: Mutex<Option<u16>>,
}

fn project_root() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .expect("ClaudeX project root is missing")
}

#[tauri::command]
#[cfg(not(mobile))]
fn start_backend(app: AppHandle, state: State<'_, BackendProcess>) -> Result<String, String> {
    let mut process = state
        .child
        .lock()
        .map_err(|_| "Backend process lock failed".to_string())?;
    if let Some(child) = process.as_mut() {
        if child.try_wait().map_err(|e| e.to_string())?.is_none() {
            let port = *state
                .port
                .lock()
                .map_err(|_| "Backend port lock failed")?
                .as_ref()
                .ok_or("Backend port is unavailable")?;
            return Ok(format!("http://127.0.0.1:{port}"));
        }
        *process = None;
    }
    *state.port.lock().map_err(|_| "Backend port lock failed")? = None;

    let port = portpicker::pick_unused_port().ok_or("Could not reserve a local port")?;
    let app_data = app.path().app_data_dir().map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&app_data).map_err(|e| e.to_string())?;

    #[cfg(debug_assertions)]
    let mut command = {
        let root = project_root();
        #[cfg(windows)]
        let python = root.join(".venv/Scripts/python.exe");
        #[cfg(not(windows))]
        let python = root.join(".venv/bin/python");
        let mut command = if python.exists() {
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
        let resource_dir = app.path().resource_dir().map_err(|e| e.to_string())?;
        #[cfg(windows)]
        let executable = resource_dir.join("resources/backend/claudex-backend.exe");
        #[cfg(not(windows))]
        let executable = resource_dir.join("resources/backend/claudex-backend");
        if !executable.is_file() {
            return Err(format!(
                "Packaged Python backend not found: {}",
                executable.display()
            ));
        }
        Command::new(executable)
    };

    command
        .env("CLAUDEX_PORT", port.to_string())
        .env("CLAUDEX_HOST", "127.0.0.1")
        .env("CLAUDEX_DATA_DIR", app_data.join("data"))
        .env("CLAUDEX_ENV_FILE", app_data.join(".env"))
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    let child = command
        .spawn()
        .map_err(|e| format!("Could not start ClaudeX Python backend: {e}"))?;
    *process = Some(child);
    *state.port.lock().map_err(|_| "Backend port lock failed")? = Some(port);
    let address = format!("127.0.0.1:{port}");
    let deadline = Instant::now() + Duration::from_secs(30);
    while Instant::now() < deadline {
        if TcpStream::connect(&address).is_ok() {
            return Ok(format!("http://{address}"));
        }
        if let Some(child) = process.as_mut() {
            if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
                *process = None;
                *state.port.lock().map_err(|_| "Backend port lock failed")? = None;
                return Err(format!(
                    "ClaudeX Python backend exited during startup ({status})."
                ));
            }
        }
        thread::sleep(Duration::from_millis(150));
    }
    if let Some(mut child) = process.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
    *state.port.lock().map_err(|_| "Backend port lock failed")? = None;
    Err("ClaudeX backend did not start within 30 seconds.".into())
}

#[tauri::command]
#[cfg(mobile)]
fn start_backend() -> Result<String, String> {
    Ok("remote".into())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(BackendProcess {
            child: Mutex::new(None),
            port: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![start_backend])
        .build(tauri::generate_context!())
        .expect("error while building ClaudeX")
        .run(|app, event| {
            if matches!(event, tauri::RunEvent::Exit) {
                if let Some(process) = app.try_state::<BackendProcess>() {
                    if let Ok(mut child) = process.child.lock() {
                        if let Some(mut child) = child.take() {
                            let _ = child.kill();
                            let _ = child.wait();
                        }
                    }
                }
            }
        });
}
