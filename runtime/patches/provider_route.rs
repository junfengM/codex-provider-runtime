// This file is injected into codex-app-server by router_manager.py.
// Keep the policy deliberately narrow: only new DeepSeek threads whose caller
// omitted a provider (or incorrectly supplied the ChatGPT default provider)
// are redirected. Explicit third-party providers remain authoritative.
pub(super) fn model_provider_for_new_thread(
    model: Option<&str>,
    model_provider: Option<String>,
) -> Option<String> {
    let is_deepseek_model = model
        .map(|model| model.starts_with("deepseek-"))
        .unwrap_or(false);
    let has_default_provider = matches!(model_provider.as_deref(), None | Some("openai"));

    if is_deepseek_model && has_default_provider {
        Some("deepseek".to_string())
    } else {
        model_provider
    }
}

#[cfg(test)]
mod tests {
    use super::model_provider_for_new_thread;

    #[test]
    fn routes_deepseek_when_provider_is_missing() {
        assert_eq!(
            model_provider_for_new_thread(Some("deepseek-v4-flash"), None),
            Some("deepseek".to_string())
        );
    }

    #[test]
    fn corrects_chatgpt_default_for_deepseek() {
        assert_eq!(
            model_provider_for_new_thread(
                Some("deepseek-v4-pro"),
                Some("openai".to_string())
            ),
            Some("deepseek".to_string())
        );
    }

    #[test]
    fn preserves_an_explicit_non_default_provider() {
        assert_eq!(
            model_provider_for_new_thread(
                Some("deepseek-v4-flash"),
                Some("private-gateway".to_string())
            ),
            Some("private-gateway".to_string())
        );
    }

    #[test]
    fn leaves_gpt_routing_unchanged() {
        assert_eq!(
            model_provider_for_new_thread(Some("gpt-5.6-sol"), None),
            None
        );
        assert_eq!(
            model_provider_for_new_thread(
                Some("gpt-5.6-sol"),
                Some("openai".to_string())
            ),
            Some("openai".to_string())
        );
    }
}
