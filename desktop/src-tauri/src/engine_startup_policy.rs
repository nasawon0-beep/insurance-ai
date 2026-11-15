// Startup ownership is distinct from readiness. Unknown listeners are never reused.
#[derive(Debug, PartialEq)]
pub(crate) enum ExistingEngine { Spawn, WaitOwned, RejectForeign }
pub(crate) fn qa_existing_engine(managed: bool, listening: bool) -> ExistingEngine {
    if managed { ExistingEngine::WaitOwned }
    else if listening { ExistingEngine::RejectForeign }
    else { ExistingEngine::Spawn }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn owned_before_bind_waits() { assert_eq!(qa_existing_engine(true,false),ExistingEngine::WaitOwned); }
    #[test] fn owned_bound_before_health_waits() { assert_eq!(qa_existing_engine(true,true),ExistingEngine::WaitOwned); }
    #[test] fn foreign_listener_is_rejected() { assert_eq!(qa_existing_engine(false,true),ExistingEngine::RejectForeign); }
    #[test] fn no_child_no_listener_spawns() { assert_eq!(qa_existing_engine(false,false),ExistingEngine::Spawn); }
}
