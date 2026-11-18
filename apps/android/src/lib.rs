use std::fs;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use reqwest::blocking::{Client, Response};
use reqwest::header::AUTHORIZATION;
use serde::{Deserialize, Serialize};
use slint::{ModelRc, SharedString, VecModel, Weak};

slint::include_modules!();

#[derive(Clone, Serialize, Deserialize)]
struct ServerConfig {
    origin: String,
    token: String,
}

struct AppState {
    config: Mutex<Option<ServerConfig>>,
    conversation_ids: Mutex<Vec<String>>,
    active_conversation: Mutex<Option<String>>,
    config_path: PathBuf,
}

#[derive(Deserialize)]
struct ConversationSummary {
    id: String,
    title: String,
}

#[derive(Deserialize)]
struct ChatMessage {
    role: String,
    content: String,
}

#[derive(Deserialize)]
struct ConversationResponse {
    messages: Vec<ChatMessage>,
}

#[derive(Deserialize)]
struct StreamEvent {
    #[serde(rename = "type")]
    kind: String,
    #[serde(default)]
    text: String,
    #[serde(default)]
    label: String,
    #[serde(default)]
    message: String,
}

fn client() -> Result<Client, String> {
    Client::builder()
        .connect_timeout(Duration::from_secs(15))
        .timeout(Duration::from_secs(600))
        .build()
        .map_err(|error| format!("Could not initialize HTTPS client: {error}"))
}

fn validate_config(origin: &str, token: &str) -> Result<ServerConfig, String> {
    let parsed = url::Url::parse(origin.trim())
        .map_err(|_| "Enter a valid HTTPS server URL.".to_string())?;
    if parsed.scheme() != "https"
        || parsed.host_str().is_none()
        || parsed.path() != "/"
        || parsed.query().is_some()
        || parsed.fragment().is_some()
        || !parsed.username().is_empty()
        || parsed.password().is_some()
    {
        return Err("Use an HTTPS server origin without a path, query, or credentials.".into());
    }
    let token = token.trim();
    if token.len() < 32 {
        return Err("The access token must contain at least 32 characters.".into());
    }
    Ok(ServerConfig {
        origin: parsed.origin().ascii_serialization(),
        token: token.to_owned(),
    })
}

fn api_request(
    config: &ServerConfig,
    path: &str,
) -> Result<reqwest::blocking::RequestBuilder, String> {
    let client = client()?;
    Ok(client
        .get(format!("{}{}", config.origin, path))
        .header(AUTHORIZATION, format!("Bearer {}", config.token)))
}

fn post_request(
    config: &ServerConfig,
    path: &str,
) -> Result<reqwest::blocking::RequestBuilder, String> {
    let client = client()?;
    Ok(client
        .post(format!("{}{}", config.origin, path))
        .header(AUTHORIZATION, format!("Bearer {}", config.token)))
}

fn response_error(response: Response) -> String {
    let status = response.status();
    let body = response.text().unwrap_or_default();
    let detail = serde_json::from_str::<serde_json::Value>(&body)
        .ok()
        .and_then(|value| {
            value
                .get("detail")
                .and_then(|detail| detail.as_str())
                .map(str::to_owned)
        })
        .unwrap_or(body);
    if detail.is_empty() {
        format!("ClaudeX server returned HTTP {status}.")
    } else {
        format!("ClaudeX server returned HTTP {status}: {detail}")
    }
}

fn save_config(path: &Path, config: &ServerConfig) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("Could not create app data folder: {error}"))?;
    }
    let temporary = path.with_extension("tmp");
    let encoded = serde_json::to_vec(config).map_err(|error| error.to_string())?;
    fs::write(&temporary, encoded)
        .map_err(|error| format!("Could not save connection settings: {error}"))?;
    fs::rename(&temporary, path)
        .map_err(|error| format!("Could not save connection settings: {error}"))
}

fn get_config(state: &AppState) -> Result<ServerConfig, String> {
    state
        .config
        .lock()
        .map_err(|_| "Connection settings are unavailable.".to_string())?
        .clone()
        .ok_or_else(|| "Connect to your ClaudeX server first.".to_string())
}

fn ui_status(ui: &Weak<MainWindow>, status: impl Into<String>) {
    let status = SharedString::from(status.into());
    let weak = ui.clone();
    let _ = slint::invoke_from_event_loop(move || {
        if let Some(ui) = weak.upgrade() {
            ui.set_status(status);
        }
    });
}

fn ui_transcript(ui: &Weak<MainWindow>, transcript: String) {
    let transcript = SharedString::from(transcript);
    let weak = ui.clone();
    let _ = slint::invoke_from_event_loop(move || {
        if let Some(ui) = weak.upgrade() {
            ui.set_transcript(transcript);
        }
    });
}

fn load_conversations(ui: Weak<MainWindow>, state: Arc<AppState>) {
    thread::spawn(move || {
        let result = (|| {
            let config = get_config(&state)?;
            let response = api_request(&config, "/api/conversations")?
                .send()
                .map_err(|error| format!("Could not load chats: {error}"))?;
            if !response.status().is_success() {
                return Err(response_error(response));
            }
            let chats = response
                .json::<Vec<ConversationSummary>>()
                .map_err(|error| format!("Could not read chat list: {error}"))?;
            let ids = chats.iter().map(|chat| chat.id.clone()).collect::<Vec<_>>();
            let titles = chats
                .iter()
                .map(|chat| SharedString::from(chat.title.as_str()))
                .collect::<Vec<_>>();
            *state
                .conversation_ids
                .lock()
                .map_err(|_| "Chat list is unavailable.".to_string())? = ids;
            Ok(titles)
        })();

        match result {
            Ok(titles) => {
                let weak = ui.clone();
                let _ = slint::invoke_from_event_loop(move || {
                    if let Some(ui) = weak.upgrade() {
                        ui.set_conversation_titles(ModelRc::new(VecModel::from(titles)));
                        ui.set_status("Connected to ClaudeX.".into());
                    }
                });
            }
            Err(error) => ui_status(&ui, error),
        }
    });
}

fn connect(ui: Weak<MainWindow>, state: Arc<AppState>, origin: String, token: String) {
    ui_status(&ui, "Connecting securely to ClaudeX…");
    thread::spawn(move || {
        let result = (|| {
            let config = validate_config(&origin, &token)?;
            let response = api_request(&config, "/api/config")?
                .send()
                .map_err(|error| format!("Could not reach ClaudeX: {error}"))?;
            if !response.status().is_success() {
                return Err(response_error(response));
            }
            let _: serde_json::Value = response
                .json()
                .map_err(|error| format!("ClaudeX returned an invalid response: {error}"))?;
            save_config(&state.config_path, &config)?;
            *state
                .config
                .lock()
                .map_err(|_| "Connection settings are unavailable.".to_string())? = Some(config);
            Ok(())
        })();

        match result {
            Ok(()) => {
                let weak = ui.clone();
                let _ = slint::invoke_from_event_loop(move || {
                    if let Some(ui) = weak.upgrade() {
                        ui.set_connected(true);
                        ui.set_status("Connected. Choose a chat or start a new one.".into());
                    }
                });
                load_conversations(ui, state);
            }
            Err(error) => ui_status(&ui, error),
        }
    });
}

fn create_conversation(ui: Weak<MainWindow>, state: Arc<AppState>) {
    ui_status(&ui, "Starting a new chat…");
    thread::spawn(move || {
        let result = (|| {
            let config = get_config(&state)?;
            let response = post_request(&config, "/api/conversations")?
                .json(&serde_json::json!({}))
                .send()
                .map_err(|error| format!("Could not start a new chat: {error}"))?;
            if !response.status().is_success() {
                return Err(response_error(response));
            }
            let conversation = response
                .json::<ConversationSummary>()
                .map_err(|error| format!("Could not read the new chat: {error}"))?;
            *state
                .active_conversation
                .lock()
                .map_err(|_| "Active chat is unavailable.".to_string())? = Some(conversation.id);
            Ok(())
        })();
        match result {
            Ok(()) => {
                ui_transcript(&ui, String::new());
                load_conversations(ui, state);
            }
            Err(error) => ui_status(&ui, error),
        }
    });
}

fn select_conversation(ui: Weak<MainWindow>, state: Arc<AppState>, index: usize) {
    let id = state
        .conversation_ids
        .lock()
        .ok()
        .and_then(|ids| ids.get(index).cloned());
    let Some(id) = id else {
        ui_status(
            &ui,
            "That chat is no longer available. Refresh and try again.",
        );
        return;
    };
    ui_status(&ui, "Loading chat…");
    thread::spawn(move || {
        let result = (|| {
            let config = get_config(&state)?;
            let response = api_request(&config, &format!("/api/conversations/{id}"))?
                .send()
                .map_err(|error| format!("Could not load chat: {error}"))?;
            if !response.status().is_success() {
                return Err(response_error(response));
            }
            let conversation = response
                .json::<ConversationResponse>()
                .map_err(|error| format!("Could not read chat: {error}"))?;
            let transcript = conversation
                .messages
                .into_iter()
                .map(|message| {
                    let speaker = if message.role == "assistant" {
                        "ClaudeX"
                    } else {
                        "You"
                    };
                    format!("{speaker}: {}", message.content)
                })
                .collect::<Vec<_>>()
                .join("\n\n");
            *state
                .active_conversation
                .lock()
                .map_err(|_| "Active chat is unavailable.".to_string())? = Some(id);
            Ok(transcript)
        })();
        match result {
            Ok(transcript) => {
                ui_transcript(&ui, transcript);
                ui_status(&ui, "Chat ready.");
            }
            Err(error) => ui_status(&ui, error),
        }
    });
}

fn send_message(ui: Weak<MainWindow>, state: Arc<AppState>, message: String, mode: String) {
    let message = message.trim().to_owned();
    if message.is_empty() {
        ui_status(&ui, "Write a message first.");
        return;
    }

    let mut transcript = ui
        .upgrade()
        .map(|window| window.get_transcript().to_string())
        .unwrap_or_default();
    if !transcript.is_empty() {
        transcript.push_str("\n\n");
    }
    transcript.push_str("You: ");
    transcript.push_str(&message);
    transcript.push_str("\n\nClaudeX: ");
    ui_transcript(&ui, transcript.clone());
    ui_status(&ui, "ClaudeX is responding…");

    thread::spawn(move || {
        let result = (|| {
            let config = get_config(&state)?;
            let conversation_id = match state
                .active_conversation
                .lock()
                .map_err(|_| "Active chat is unavailable.".to_string())?
                .clone()
            {
                Some(id) => id,
                None => {
                    let response = post_request(&config, "/api/conversations")?
                        .json(&serde_json::json!({}))
                        .send()
                        .map_err(|error| format!("Could not start a chat: {error}"))?;
                    if !response.status().is_success() {
                        return Err(response_error(response));
                    }
                    let chat = response
                        .json::<ConversationSummary>()
                        .map_err(|error| format!("Could not read new chat: {error}"))?;
                    *state
                        .active_conversation
                        .lock()
                        .map_err(|_| "Active chat is unavailable.".to_string())? =
                        Some(chat.id.clone());
                    chat.id
                }
            };

            let response = post_request(&config, "/api/chat")?
                .json(&serde_json::json!({
                    "conversation_id": conversation_id,
                    "message": message,
                    "mode": mode,
                    "council_mode": mode == "council",
                    "auto_think": true,
                    "auto_memory": true,
                    "attachment_ids": []
                }))
                .send()
                .map_err(|error| format!("Could not send message: {error}"))?;
            if !response.status().is_success() {
                return Err(response_error(response));
            }

            read_stream(response, &ui, &mut transcript)?;
            load_conversations(ui.clone(), Arc::clone(&state));
            Ok(())
        })();

        if let Err(error) = result {
            ui_status(&ui, error);
        } else {
            ui_status(&ui, "Response complete.");
        }
    });
}

fn read_stream(
    response: Response,
    ui: &Weak<MainWindow>,
    transcript: &mut String,
) -> Result<(), String> {
    let mut reader = BufReader::new(response);
    let mut line = String::new();
    loop {
        line.clear();
        let count = reader
            .read_line(&mut line)
            .map_err(|error| format!("Response stream failed: {error}"))?;
        if count == 0 {
            break;
        }
        let Some(data) = line.strip_prefix("data:") else {
            continue;
        };
        let event = serde_json::from_str::<StreamEvent>(data.trim())
            .map_err(|error| format!("ClaudeX sent an invalid response event: {error}"))?;
        match event.kind.as_str() {
            "token" => {
                transcript.push_str(&event.text);
                ui_transcript(ui, transcript.clone());
            }
            "status" => ui_status(ui, event.label),
            "error" => return Err(event.message),
            _ => {}
        }
    }
    Ok(())
}

fn launch(config_dir: PathBuf) -> Result<(), slint::PlatformError> {
    let ui = MainWindow::new()?;
    let state = Arc::new(AppState {
        config: Mutex::new(None),
        conversation_ids: Mutex::new(Vec::new()),
        active_conversation: Mutex::new(None),
        config_path: config_dir.join("connection.json"),
    });

    {
        let weak = ui.as_weak();
        let state = Arc::clone(&state);
        ui.on_connect_request(move |origin, token| {
            connect(
                weak.clone(),
                Arc::clone(&state),
                origin.to_string(),
                token.to_string(),
            );
        });
    }
    {
        let weak = ui.as_weak();
        let state = Arc::clone(&state);
        ui.on_new_chat_request(move || create_conversation(weak.clone(), Arc::clone(&state)));
    }
    {
        let weak = ui.as_weak();
        let state = Arc::clone(&state);
        ui.on_refresh_chats_request(move || load_conversations(weak.clone(), Arc::clone(&state)));
    }
    {
        let weak = ui.as_weak();
        let state = Arc::clone(&state);
        ui.on_conversation_selected(move |index| {
            select_conversation(weak.clone(), Arc::clone(&state), index as usize);
        });
    }
    {
        let weak = ui.as_weak();
        let state = Arc::clone(&state);
        ui.on_send_request(move |message, mode| {
            send_message(
                weak.clone(),
                Arc::clone(&state),
                message.to_string(),
                mode.to_string(),
            );
        });
    }
    {
        let weak = ui.as_weak();
        ui.on_settings_request(move || {
            if let Some(ui) = weak.upgrade() {
                ui.set_connected(false);
                ui.set_status("Update the HTTPS server URL or access token.".into());
            }
        });
    }

    if let Ok(bytes) = fs::read(&state.config_path) {
        if let Ok(saved) = serde_json::from_slice::<ServerConfig>(&bytes) {
            ui.set_server_url(saved.origin.clone().into());
            ui.set_access_token(saved.token.clone().into());
            connect(ui.as_weak(), Arc::clone(&state), saved.origin, saved.token);
        }
    }

    ui.run()
}

#[cfg(target_os = "android")]
#[unsafe(no_mangle)]
fn android_main(app: slint::android::AndroidApp) {
    if let Err(error) = slint::android::init(app.clone()) {
        eprintln!("Could not initialize Slint on Android: {error}");
        return;
    }
    let Some(data_dir) = app.internal_data_path() else {
        eprintln!("Android did not provide private app storage.");
        return;
    };
    if let Err(error) = launch(data_dir) {
        eprintln!("ClaudeX stopped: {error}");
    }
}
