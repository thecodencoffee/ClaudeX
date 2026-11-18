use std::env;
use std::fs;
use std::path::PathBuf;

fn main() {
    let manifest = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").expect("manifest directory"));
    let sidecar_name = if cfg!(windows) {
        "claudex-backend.exe"
    } else {
        "claudex-backend"
    };
    let source = manifest
        .join("../src-tauri/resources/backend")
        .join(sidecar_name);
    println!("cargo:rerun-if-changed={}", source.display());

    if !source.is_file() {
        return;
    }

    let out_dir = PathBuf::from(env::var_os("OUT_DIR").expect("build output directory"));
    let profile_dir = out_dir
        .ancestors()
        .nth(3)
        .expect("Cargo output directory layout");
    let destination = profile_dir.join("resources/backend");
    fs::create_dir_all(&destination).expect("create desktop resources directory");
    fs::copy(&source, destination.join(sidecar_name)).expect("copy Python backend sidecar");
}
