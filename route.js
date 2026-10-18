// CodexDesktopDeepSeekFlashPatch: local provider routing and model-specific defaults.
function codexPatchModelRoutes() {
  return /* MODEL_ROUTES_BEGIN */{"deepseek-flash":{"provider":"deepseek-desktop"},"grok-4.5":{"provider":"easycli-desktop","context_window":500000,"max_context_window":500000,"compact_limit":450000},"grok-4.7":{"provider":"easycli-desktop","context_window":500000,"max_context_window":500000,"compact_limit":450000},"gemini-3.8-flash-high":{"provider":"easycli-desktop","context_window":1048576,"max_context_window":1048576,"compact_limit":943718},"claude-opus-4-6-thinking":{"provider":"easycli-desktop","context_window":1000000,"max_context_window":1000000,"compact_limit":900000}}/* MODEL_ROUTES_END */;
}
function codexPatchAppServerParams(method, params, hostId) {
  if (hostId !== 'local' || !params || typeof params !== 'object') return params;
  if (method === 'thread/list' && params.modelProviders == null)
    return {...params, modelProviders: []};
  if (method !== 'thread/start') return params;
  const routes = codexPatchModelRoutes();
  const route = Object.prototype.hasOwnProperty.call(routes, params.model) ? routes[params.model] : null;
  if (!route) return params;
  const provider = route.provider;
  if (params.modelProvider != null && params.modelProvider !== 'openai' &&
      !(route.context_window && params.modelProvider === provider)) return params;
  const config = {...params.config, web_search: 'disabled'};
  if (route.context_window) {
    const requested = config.model_context_window;
    const window = Number.isSafeInteger(requested) && requested > 0
      ? Math.min(requested, route.max_context_window) : route.context_window;
    config.model_context_window = window;
    const compact = Math.min(route.compact_limit, Math.floor(window * 0.9));
    const requestedCompact = config.model_auto_compact_token_limit;
    config.model_auto_compact_token_limit = Number.isSafeInteger(requestedCompact) && requestedCompact > 0
      ? Math.min(requestedCompact, compact) : compact;
  }
  return {...params, modelProvider: provider, serviceTier: null, config};
}
