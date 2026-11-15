fn main() {
    use sha2::{Digest,Sha256};
    let worker="binaries/maintenance/maintenance-worker.exe";
    let mut source=Sha256::new();
    for name in ["account_maintenance_worker.py","account_maintenance.py","account_migration.py","account_workspace.py","protected_business_key.py","database/crypto.py","rag/store.py","maintenance-worker.spec"]{
        let path=format!("../../local-engine/{name}");println!("cargo:rerun-if-changed={path}");
        source.update(name.as_bytes());source.update([0]);source.update(std::fs::read(path).expect("maintenance source missing"));source.update([0]);
    }
    println!("cargo:rerun-if-changed=binaries/maintenance/source.sha256");
    let packaged_source=std::fs::read_to_string("binaries/maintenance/source.sha256").expect("Rebuild maintenance worker with scripts/build_maintenance_worker.py");
    assert_eq!(packaged_source,format!("{:x}",source.finalize()),"Maintenance source changed: rebuild worker before application");
    println!("cargo:rerun-if-changed={worker}");
    for name in ["VITE_MOADOOM_SUPABASE_URL","VITE_MOADOOM_SUPABASE_PUBLISHABLE_KEY","VITE_MOADOOM_WORKSPACE_PUBLIC_KEYS","VITE_MOADOOM_MAINTENANCE_PUBLIC_KEYS"]{println!("cargo:rerun-if-env-changed={name}");}
    let bytes=std::fs::read(worker).expect("Run scripts/build_maintenance_worker.py first; unpinned maintenance worker is forbidden");
    println!("cargo:rustc-env=MOADOOM_PACKAGED_WORKER_SHA256={:x}",Sha256::digest(bytes));
    tauri_build::build()
}
