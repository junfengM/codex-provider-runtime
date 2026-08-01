// This file is injected into codex-app-server by router_manager.py.
// Integrated model contract: DeepSeek V4 Flash-0731 only.
// Keep the routing policy deliberately narrow: only new V4 Flash threads whose
// caller omitted a provider (or incorrectly supplied the ChatGPT default
// provider) are redirected. Explicit third-party providers remain authoritative.
pub(super) fn model_provider_for_new_thread(
    model: Option<&str>,
    model_provider: Option<String>,
) -> Option<String> {
    let is_deepseek_model = model == Some("deepseek-v4-flash");
    let has_default_provider = matches!(model_provider.as_deref(), None | Some("openai"));

    if is_deepseek_model && has_default_provider {
        Some("deepseek".to_string())
    } else {
        model_provider
    }
}

// App Server's public thread/list contract says an omitted, null, or empty
// modelProviders filter includes every provider. Some bundled builds instead
// default an omitted filter to the configured provider, which hides valid
// third-party threads from Desktop and phone Remote history views.
pub(super) fn model_provider_filter_for_thread_list(
    model_providers: Option<Vec<String>>,
) -> Option<Vec<String>> {
    model_providers.filter(|providers| !providers.is_empty())
}

#[cfg(test)]
mod tests {
    use super::model_provider_filter_for_thread_list;
    use super::model_provider_for_new_thread;

    #[test]
    fn routes_deepseek_when_provider_is_missing() {
        assert_eq!(
            model_provider_for_new_thread(Some("deepseek-v4-flash"), None),
            Some("deepseek".to_string())
        );
    }

    #[test]
    fn corrects_chatgpt_default_for_flash() {
        assert_eq!(
            model_provider_for_new_thread(
                Some("deepseek-v4-flash"),
                Some("openai".to_string())
            ),
            Some("deepseek".to_string())
        );
    }

    #[test]
    fn does_not_route_unintegrated_deepseek_models() {
        assert_eq!(
            model_provider_for_new_thread(
                Some("deepseek-v4-pro"),
                Some("openai".to_string())
            ),
            Some("openai".to_string())
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

    #[test]
    fn omitted_thread_list_filter_includes_all_providers() {
        assert_eq!(model_provider_filter_for_thread_list(None), None);
    }

    #[test]
    fn empty_thread_list_filter_includes_all_providers() {
        assert_eq!(model_provider_filter_for_thread_list(Some(Vec::new())), None);
    }

    #[test]
    fn explicit_thread_list_filter_remains_authoritative() {
        assert_eq!(
            model_provider_filter_for_thread_list(Some(vec!["deepseek".to_string()])),
            Some(vec!["deepseek".to_string()])
        );
    }
}
