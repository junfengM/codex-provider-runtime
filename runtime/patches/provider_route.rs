// This file is injected into codex-app-server by router_manager.py.
// Integrated model contract: DeepSeek V4 Flash-0731 and V4 Pro-0813.
// Keep the routing policy deliberately narrow: only supported V4 threads whose
// caller omitted a provider (or incorrectly supplied the ChatGPT default
// provider) are redirected. Explicit third-party providers remain authoritative.
pub(super) fn model_provider_for_new_thread(
    model: Option<&str>,
    model_provider: Option<String>,
) -> Option<String> {
    model_provider_for_supported_model(model, model_provider)
}

// A resumed thread can carry the model selected by Desktop/Remote while
// omitting modelProvider. In that case Config would otherwise fall back to
// the global OpenAI provider before the first post-resume turn. Apply the same
// exact-model policy to the resume path without rewriting stored metadata.
pub(super) fn model_provider_for_resume(
    model: Option<&str>,
    model_provider: Option<String>,
) -> Option<String> {
    model_provider_for_supported_model(model, model_provider)
}

fn model_provider_for_supported_model(
    model: Option<&str>,
    model_provider: Option<String>,
) -> Option<String> {
    let is_deepseek_model =
        matches!(model, Some("deepseek-v4-flash" | "deepseek-v4-pro"));
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
    use super::model_provider_for_resume;

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
    fn corrects_chatgpt_default_when_resuming_flash() {
        assert_eq!(
            model_provider_for_resume(Some("deepseek-v4-flash"), None),
            Some("deepseek".to_string())
        );
        assert_eq!(
            model_provider_for_resume(
                Some("deepseek-v4-flash"),
                Some("openai".to_string())
            ),
            Some("deepseek".to_string())
        );
    }

    #[test]
    fn routes_pro_for_new_and_resumed_threads() {
        assert_eq!(
            model_provider_for_new_thread(
                Some("deepseek-v4-pro"),
                Some("openai".to_string())
            ),
            Some("deepseek".to_string())
        );
        assert_eq!(
            model_provider_for_resume(Some("deepseek-v4-pro"), None),
            Some("deepseek".to_string())
        );
    }

    #[test]
    fn does_not_route_unknown_deepseek_models() {
        assert_eq!(
            model_provider_for_new_thread(
                Some("deepseek-v5-preview"),
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
